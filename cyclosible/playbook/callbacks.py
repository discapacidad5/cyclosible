from ansible import utils
import sys
import getpass
import fnmatch
from ansible import constants
import locale
from ansible.module_utils import basic
from ansible.utils.unicode import to_unicode, to_bytes
from ansible.callbacks import (DefaultRunnerCallbacks, call_callback_module, banner, log_flock, log_unflock)
from ansible.color import stringc


def display(msg, tmpfile, color=None, stderr=False, screen_only=False, log_only=False, runner=None):
    # prevent a very rare case of interlaced multiprocess I/O
    log_flock(runner)
    while msg.startswith("\n"):
        msg = msg.replace("\n", "")
    msg2 = msg
    if color:
        msg2 = stringc(msg, color)
    tmpfile.write(msg2 + '\n')
    log_unflock(runner)


class PlaybookRunnerCallbacks(DefaultRunnerCallbacks):
    """ callbacks used for Runner() from /usr/bin/ansible-playbook """

    def __init__(self, stats, tmpfile, verbose=None):

        if verbose is None:
            verbose = utils.VERBOSITY

        self.tmpfile = tmpfile
        self.verbose = verbose
        self.stats = stats
        self._async_notified = {}
        super(DefaultRunnerCallbacks, self).__init__()

    def on_unreachable(self, host, results):
        if self.runner.delegate_to:
            host = '%s -> %s' % (host, self.runner.delegate_to)

        item = None
        if type(results) == dict:
            item = results.get('item', None)
            if isinstance(item, unicode):
                item = utils.unicode.to_bytes(item)
            results = basic.json_dict_unicode_to_bytes(results)
        else:
            results = utils.unicode.to_bytes(results)
        host = utils.unicode.to_bytes(host)
        if item:
            msg = "fatal: [%s] => (item=%s) => %s" % (host, item, results)
        else:
            msg = "fatal: [%s] => %s" % (host, results)
        display(msg, color='red', runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_unreachable(host, results)

    def on_failed(self, host, results, ignore_errors=False):
        if self.runner.delegate_to:
            host = '%s -> %s' % (host, self.runner.delegate_to)

        results2 = results.copy()
        results2.pop('invocation', None)

        item = results2.get('item', None)
        parsed = results2.get('parsed', True)
        module_msg = ''
        if not parsed:
            module_msg = results2.pop('msg', None)
        stderr = results2.pop('stderr', None)
        stdout = results2.pop('stdout', None)
        returned_msg = results2.pop('msg', None)

        if item:
            msg = "failed: [%s] => (item=%s) => %s" % (host, item, utils.jsonify(results2))
        else:
            msg = "failed: [%s] => %s" % (host, utils.jsonify(results2))
        display(msg, color='red', runner=self.runner, tmpfile=self.tmpfile)

        if stderr:
            display("stderr: %s" % stderr, color='red', runner=self.runner, tmpfile=self.tmpfile)
        if stdout:
            display("stdout: %s" % stdout, color='red', runner=self.runner, tmpfile=self.tmpfile)
        if returned_msg:
            display("msg: %s" % returned_msg, color='red', runner=self.runner, tmpfile=self.tmpfile)
        if not parsed and module_msg:
            display(module_msg, color='red', runner=self.runner, tmpfile=self.tmpfile)
        if ignore_errors:
            display("...ignoring", color='cyan', runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_failed(host, results, ignore_errors=ignore_errors)

    def on_ok(self, host, host_result):
        if self.runner.delegate_to:
            host = '%s -> %s' % (host, self.runner.delegate_to)

        item = host_result.get('item', None)

        host_result2 = host_result.copy()
        host_result2.pop('invocation', None)
        verbose_always = host_result2.pop('verbose_always', False)
        changed = host_result.get('changed', False)
        ok_or_changed = 'ok'
        if changed:
            ok_or_changed = 'changed'

        # show verbose output for non-setup module results if --verbose is used
        msg = ''
        if (not self.verbose or host_result2.get("verbose_override", None) is not
                None) and not verbose_always:
            if item:
                msg = "%s: [%s] => (item=%s)" % (ok_or_changed, host, item)
            else:
                if 'ansible_job_id' not in host_result or 'finished' in host_result:
                    msg = "%s: [%s]" % (ok_or_changed, host)
        else:
            # verbose ...
            if item:
                msg = "%s: [%s] => (item=%s) => %s" % (ok_or_changed, host, item,
                                                       utils.jsonify(host_result2, format=verbose_always))
            else:
                if 'ansible_job_id' not in host_result or 'finished' in host_result2:
                    msg = "%s: [%s] => %s" % (ok_or_changed, host, utils.jsonify(host_result2, format=verbose_always))

        if msg != '':
            if not changed:
                display(msg, color='green', runner=self.runner, tmpfile=self.tmpfile)
            else:
                display(msg, color='yellow', runner=self.runner, tmpfile=self.tmpfile)
        if constants.COMMAND_WARNINGS and 'warnings' in host_result2 and host_result2['warnings']:
            for warning in host_result2['warnings']:
                display("warning: %s" % warning, color='purple', runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_ok(host, host_result)

    def on_skipped(self, host, item=None):
        if self.runner.delegate_to:
            host = '%s -> %s' % (host, self.runner.delegate_to)

        if constants.DISPLAY_SKIPPED_HOSTS:
            msg = ''
            if item:
                msg = "skipping: [%s] => (item=%s)" % (host, item)
            else:
                msg = "skipping: [%s]" % host
            display(msg, color='cyan', runner=self.runner, tmpfile=self.tmpfile)
            super(PlaybookRunnerCallbacks, self).on_skipped(host, item)

    def on_no_hosts(self):
        display("FATAL: no hosts matched or all hosts have already failed -- aborting\n", color='red',
                runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_no_hosts()

    def on_async_poll(self, host, res, jid, clock):
        if jid not in self._async_notified:
            self._async_notified[jid] = clock + 1
        if self._async_notified[jid] > clock:
            self._async_notified[jid] = clock
            msg = "<job %s> polling, %ss remaining" % (jid, clock)
            display(msg, color='cyan', runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_async_poll(host, res, jid, clock)

    def on_async_ok(self, host, res, jid):
        if jid:
            msg = "<job %s> finished on %s" % (jid, host)
            display(msg, color='cyan', runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_async_ok(host, res, jid)

    def on_async_failed(self, host, res, jid):
        msg = "<job %s> FAILED on %s" % (jid, host)
        display(msg, color='red', stderr=True, runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_async_failed(host, res, jid)

    def on_file_diff(self, host, diff):
        display(utils.get_diff(diff), runner=self.runner, tmpfile=self.tmpfile)
        super(PlaybookRunnerCallbacks, self).on_file_diff(host, diff)


class PlaybookCallbacks(object):
    """ playbook.py callbacks used by /usr/bin/ansible-playbook """

    def __init__(self, tmpfile, verbose=False):
        self.tmpfile = tmpfile
        self.verbose = verbose

    def on_start(self):
        call_callback_module('playbook_on_start')

    def on_notify(self, host, handler):
        call_callback_module('playbook_on_notify', host, handler)

    def on_no_hosts_matched(self):
        display("skipping: no hosts matched", color='cyan', tmpfile=self.tmpfile)
        call_callback_module('playbook_on_no_hosts_matched')

    def on_no_hosts_remaining(self):
        display("\nFATAL: all hosts have already failed -- aborting", color='red', tmpfile=self.tmpfile)
        call_callback_module('playbook_on_no_hosts_remaining')

    def on_task_start(self, name, is_conditional):
        name = utils.unicode.to_bytes(name)
        msg = "TASK: [%s]" % name
        if is_conditional:
            msg = "NOTIFIED: [%s]" % name

        if hasattr(self, 'start_at'):
            self.start_at = utils.unicode.to_bytes(self.start_at)
            if name == self.start_at or fnmatch.fnmatch(name, self.start_at):
                # we found out match, we can get rid of this now
                del self.start_at
            elif self.task.role_name:
                # handle tasks prefixed with rolenames
                actual_name = name.split('|', 1)[1].lstrip()
                if actual_name == self.start_at or fnmatch.fnmatch(actual_name, self.start_at):
                    del self.start_at

        if hasattr(self, 'start_at'):  # we still have start_at so skip the task
            self.skip_task = True
        elif hasattr(self, 'step') and self.step:
            if isinstance(name, str):
                name = utils.unicode.to_unicode(name)
            msg = u'Perform task: %s (y/n/c): ' % name
            if sys.stdout.encoding:
                msg = to_bytes(msg, sys.stdout.encoding)
            else:
                msg = to_bytes(msg)
            resp = raw_input(msg)
            if resp.lower() in ['y', 'yes']:
                self.skip_task = False
                display(banner(msg), tmpfile=self.tmpfile)
            elif resp.lower() in ['c', 'continue']:
                self.skip_task = False
                self.step = False
                display(banner(msg), tmpfile=self.tmpfile)
            else:
                self.skip_task = True
        else:
            self.skip_task = False
            display(banner(msg), tmpfile=self.tmpfile)

        call_callback_module('playbook_on_task_start', name, is_conditional)

    def on_vars_prompt(self, varname, private=True, prompt=None, encrypt=None, confirm=False, salt_size=None,
                       salt=None, default=None):

        if prompt and default is not None:
            msg = "%s [%s]: " % (prompt, default)
        elif prompt:
            msg = "%s: " % prompt
        else:
            msg = 'input for %s: ' % varname

        def do_prompt(prompt, private):
            if sys.stdout.encoding:
                msg = prompt.encode(sys.stdout.encoding)
            else:
                # when piping the output, or at other times when stdout
                # may not be the standard file descriptor, the stdout
                # encoding may not be set, so default to something sane
                msg = prompt.encode(locale.getpreferredencoding())
            if private:
                return getpass.getpass(msg)
            return raw_input(msg)

        if confirm:
            while True:
                result = do_prompt(msg, private)
                second = do_prompt("confirm " + msg, private)
                if result == second:
                    break
                display("***** VALUES ENTERED DO NOT MATCH ****", tmpfile=self.tmpfile)
        else:
            result = do_prompt(msg, private)

        # if result is false and default is not None
        if not result and default is not None:
            result = default

        if encrypt:
            result = utils.do_encrypt(result, encrypt, salt_size, salt)

        # handle utf-8 chars
        result = to_unicode(result, errors='strict')
        call_callback_module('playbook_on_vars_prompt', varname, private=private, prompt=prompt,
                             encrypt=encrypt, confirm=confirm, salt_size=salt_size, salt=None, default=default
                             )

        return result

    def on_setup(self):
        display(banner("GATHERING FACTS"), tmpfile=self.tmpfile)
        call_callback_module('playbook_on_setup')

    def on_import_for_host(self, host, imported_file):
        msg = "%s: importing %s" % (host, imported_file)
        display(msg, color='cyan', tmpfile=self.tmpfile)
        call_callback_module('playbook_on_import_for_host', host, imported_file)

    def on_not_import_for_host(self, host, missing_file):
        msg = "%s: not importing file: %s" % (host, missing_file)
        display(msg, color='cyan', tmpfile=self.tmpfile)
        call_callback_module('playbook_on_not_import_for_host', host, missing_file)

    def on_play_start(self, name):
        display(banner("PLAY [%s]" % name), tmpfile=self.tmpfile)
        call_callback_module('playbook_on_play_start', name)

    def on_stats(self, stats):
        call_callback_module('playbook_on_stats', stats)