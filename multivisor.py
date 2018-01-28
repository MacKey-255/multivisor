#!/usr/bin/env python

from gevent.monkey import patch_all
patch_all(thread=False)

import logging
import weakref
import functools
from collections import OrderedDict
from ConfigParser import SafeConfigParser
from xmlrpclib import ServerProxy as _ServerProxy

from louie import send, connect
from flask import Flask, render_template, Response, request, json
from gevent import queue, spawn, sleep, joinall, lock
from supervisor.states import RUNNING_STATES


class ServerProxy(_ServerProxy):
    class Wrap(object):

        def __init__(self, lock, method):
            self.__lock = lock
            self.__method = method

        def __getattr__(self, name):
            return type(self)(self.__lock, getattr(self.__method, name))

        def __call__(self, *args):
            with self.__lock:
                return self.__method(*args)

    def __init__(self, *args, **kwargs):
        self.__lock = lock.RLock()
        _ServerProxy.__init__(self, *args, **kwargs)

    def __getattr__(self, name):
        method = _ServerProxy.__getattr__(self, name)
        return self.Wrap(self.__lock, method)


class Supervisor(dict):

    Null = {
        'identification': None,
        'api_version': None,
        'version': None,
        'supervisor_version': None,
        'processes': {},
        'running': False,
    }

    def __init__(self, *args, **kwargs):
        super(Supervisor, self).__init__(*args, **kwargs)
        self.log = logging.getLogger(self['name'])
        self.username = self.pop('username')
        self.password = self.pop('password')
        credentials = ''
        if self.username and self.password:
            credentials = '{0}:{1}@'.format(self.username, self.password)
        address = 'http://{0}{1}:{2}/RPC2'.format(credentials, self['host'],
                                                  self['port'])
        self.server = ServerProxy(address)

    @property
    def supervisor(self):
        return self.server.supervisor

    def update_info(self):
        try:
            pid = self.supervisor.getPID()
        except:
            pid = None
        return self._update_info(pid)

    def _update_info(self, pid):
        self.log.debug('updating')
        supervisor_name = self['name']
        supervisor = self.supervisor
        if pid != self.get('pid'):
            self['pid'] = pid
            if pid is None: # server shutdown
                self.update(self.Null)
            else:
                self['running'] = True
                self['identification'] = supervisor.getIdentification()
                self['api_version'] = supervisor.getAPIVersion()
                self['version'] = supervisor.getVersion()
                self['supervisor_version'] = supervisor.getSupervisorVersion()
            modified = True
        else:
            modified = False
        processes = {}
        if pid is not None:
            for proc in supervisor.getAllProcessInfo():
                process = Process(self, proc)
                processes[process['uid']] = process
        old_processes = self.pop('processes', None)
        self['processes'] = processes
        modified |= processes != old_processes
        return modified


class Process(dict):

    def __init__(self, supervisor, *args, **kwargs):
        super(Process, self).__init__(*args, **kwargs)
        full_name = self['group'] + ':' + self['name']
        self.log = supervisor.log.getChild(full_name)
        self.supervisor = weakref.proxy(supervisor)
        self['full_name'] = full_name
        self['running'] = self['state'] in RUNNING_STATES
        self['supervisor'] = supervisor['name']
        self['host'] = supervisor['host']
        self['uid'] = full_name + '@' + self['supervisor']

    @property
    def server(self):
        return self.supervisor.server.supervisor

    @property
    def full_name(self):
        return self['full_name']

    def update_info(self):
        info = self.server.getProcessInfo(self.full_name)
        self.update(info)
        self['running'] = self['state'] in RUNNING_STATES

    def restart(self, wait=True):
        self.log.info('Restarting')
        if self['running']:
            self.stop()
        self.server.startProcess(self.full_name, wait)

    def stop(self, wait=True):
        self.log.info('Stopping')
        self.server.stopProcess(self.full_name, wait)

    def __eq__(self, proc):
        p1, p2 = dict(self), dict(proc)
        p1.pop('description')
        p1.pop('now')
        p2.pop('description')
        p2.pop('now')
        return p1 == p2

# Configuration

def load_config(config_file):
    parser = SafeConfigParser()
    parser.read(config_file)
    dft_global = dict(name='multivisor')
    dft_supervisor = dict(event_port=None,
                          username=None,
                          password=None,
                          port=9001,
                          tags=())

    supervisors = {}
    config = dict(dft_global, supervisors=supervisors)
    config.update(parser.items('global'))
    for section in parser.sections():
        if not section.startswith('supervisor:'):
            continue
        name = section[len('supervisor:'):]
        kwargs = dict(dft_supervisor, name=name, host=name)
        kwargs.update(dict(parser.items(section)))
        supervisor = Supervisor(kwargs)
        supervisors[name] = supervisor
    return config


class Multivisor:

    def __init__(self, options):
        self.options = options
        self._config = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config(self.options.config_file)
            self.poll_supervisors()
        return self._config

    def reload_config(self):
        self._config = None
        return self.config

    @property
    def supervisors(self):
        return self.config['supervisors']

    def poll_supervisor(self, supervisor):
        modified = supervisor.update_info()
        if modified:
            logging.info('supervisor %r modified', supervisor['name'])
            send('supervisor_event', self, supervisor)

    def poll_supervisors(self):
        tasks = [spawn(self.poll_supervisor, supervisor)
                 for supervisor in self.supervisors.values()]
        joinall(tasks)

    def get_supervisor(self, name):
        return self.supervisors[supervisor]

    def get_process(self, uid):
        _, supervisor = uid.split('@', 1)
        return self.supervisors[supervisor]['processes'][uid]

    def run_forever(self):
        while True:
            self.poll_supervisors()
            sleep(self.options.poll_period)


app = Flask(__name__,
            static_folder='./dist/static',
            template_folder='./dist')


@app.route("/")
def index():
#    return app.send_static_file('index.html')
    return render_template('index.html')


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return render_template("index.html")


@app.route("/admin/reload")
def reload_config():
    app.multivisor.reload_config()
    return 'OK'


@app.route("/refresh")
def refresh():
    app.multivisor.poll_supervisors()
    return json.dumps(app.multivisor.config)


@app.route("/data")
def data():
    return json.dumps(app.multivisor.config)


@app.route("/restart_process", methods=['POST'])
def restart_process():
    process = app.multivisor.get_process(request.form['uid'])
    wait = request.form.get('wait', True)
    process.restart(wait=wait)
    if wait:
        process.update_info()
        return json.dumps(process)
    return 'OK'


@app.route("/stop_process", methods=['POST'])
def stop_process():
    process = app.multivisor.get_process(request.form['uid'])
    wait = request.form.get('wait', True)
    process.stop(wait=wait)
    if wait:
        process.update_info()
        return json.dumps(process)
    return 'OK'


@app.route("/process/<uid>")
def process_info(uid):
    process = app.multivisor.get_process(uid)
    process.update_info()
    return json.dumps(process)


@app.route('/stream')
def stream():
    app.logger
    event_queue = queue.Queue()
    def event_stream():
        for event in event_queue:
            yield json.dumps(event)
    connect(event_queue.put, signal='supervisor_event', sender=app.multivisor)
    return Response(event_stream(),
                    mimetype="text/event-stream")


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', help='web port', type=int,
                        default=22000)
    parser.add_argument('-c', help='configuration file',
                        dest='config_file',
                        default='/etc/multivisor.conf')
    parser.add_argument('--log-level', help='log level', type=str,
                        default='INFO',
                        choices=['DEBUG', 'INFO', 'WARN', 'ERROR'])
    parser.add_argument('--poll-period', help='polling period(s)', type=float,
                        default=2)
    options = parser.parse_args(args)

    log_level = getattr(logging, options.log_level.upper())
    log_fmt = '%(levelname)s %(asctime)-15s %(name)s: %(message)s'
    logging.basicConfig(level=log_level, format=log_fmt)

    app.multivisor = Multivisor(options)

    app_task = spawn(app.multivisor.run_forever)

    from gevent.wsgi import WSGIServer
    http_server = WSGIServer(('', options.port), application=app)
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        logging.info('Ctrl-C pressed. Bailing out')


if __name__ == "__main__":
    main()
