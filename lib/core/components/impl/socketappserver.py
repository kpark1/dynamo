import os
import socket
import time
import thread
import threading
import multiprocessing
import shutil
import ssl
import Queue

from dynamo.core.components.appserver import AppServer
from dynamo.core.components.impl.socketconsole import SocketDynamoConsole
from dynamo.core.manager import ServerManager

DYNAMO_PORT = 39626
DN_TRANSLATION = {'domainComponent': 'DC', 'organizationalUnitName': 'OU', 'commonName': 'CN'}

class SocketIO(object):
    def __init__(self, conn, addr):
        self.conn = conn
        self.host = addr[0]
        self.port = addr[1]

    def send(self, status, message = ''):
        """
        Send a JSON with format {'status': status, 'message': message}. If status is not OK, log
        the message.
        """

        if status != 'OK':
            LOG.error('Response to %s:%d: %s', self.host, self.port, message)

        bytes = json.dumps({'status': status, 'message': message})
        try:
            self.conn.sendall('%d %s' % (len(bytes), bytes))
        except:
            pass

    def recv(self):
        """
        Read a message possibly split in multiple transmissions. The message must have a form or a decimal
        number corresponding to the length of the content, followed by a space, and the content in JSON.
        """

        data = ''
        while True:
            try:
                bytes = self.conn.recv(2048)
            except socket.error:
                break
            if not bytes:
                break

            if not data:
                # first communication
                length, _, bytes = bytes.partition(' ')
                length = int(length)

            data += bytes

            if len(data) >= length:
                # really should be == but to be prepared for malfunction
                break

        try:
            return json.loads(data)
        except:
            self.send('failed', 'Ill-formatted data')
            raise RuntimeError()

def tail_follow(source_path, stream, stop_reading):
    ## tail -f emulation
    while True:
        if os.path.exists(source_path):
            break

        if stop_reading.is_set():
            return

        time.sleep(0.5)

    with open(source_path) as source:
        while True:
            if stop_reading.is_set():
                return

            pos = source.tell()
            line = source.readline()
            if not line:
                source.seek(pos)
                time.sleep(0.5)
            else:
                stream.sendall(line)


class SocketAppServer(AppServer):
    """
    Sub-server owned by the main Dynamo server to serve application requests.
    """

    def __init__(self, dynamo_server, config):
        AppServer.__init__(self, dynamo_server, config)

        # OpenSSL cannot authenticate with certificate proxies without this environment variable
        os.environ['OPENSSL_ALLOW_PROXY_CERTS'] = '1'

        if 'capath' in config:
            # capath only supported in SSLContext (pythonn 2.7)
            context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            context.load_cert_chain(config.certfile, keyfile = config.keyfile)
            context.load_verify_locations(capath = config.capath)
            context.verify_mode = ssl.CERT_REQUIRED
            self._sock = context.wrap_socket(socket.socket(socket.AF_INET), server_side = True)
        else:
            self._sock = ssl.wrap_socket(socket.socket(socket.AF_INET), server_side = True,
                certfile = config.certfile, keyfile = config.keyfile,
                cert_reqs = ssl.CERT_REQUIRED, ca_certs = config.cafile)

        self._sock.bind(('', DYNAMO_PORT))
        self._sock.listen(5)

    def start(self):
        """Start a daemon thread that runs the accept loop and return."""

        th = threading.Thread(target = self._accept_applications)
        th.daemon = True
        th.start()

    def stop(self):
        """Shut down the socket."""

        self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()

    def _accept_applications(self):
        """Infinite loop to serve incoming connections."""

        while True:
            # blocks until there is a connection
            # keeps blocking when socket is closed
            select.select([self._sock], [], [])
            conn, addr = self._sock.accept()
            thread.start_new_thread(self._process_application, (conn, addr))

    def _process_application(self, conn, addr):
        """
        Communicate with the client and determine server actions.
        Communication is always conversational, starting with the client. This means recvmsg()
        can assume only one message will be sent in a single string (could still be split into
        multiple transmissions). We use a rudimentary protocol of preceding the message with
        the message length in decimal integer and a space (see SocketIO implementation).
        """

        io = SocketIO(conn, addr)
        master = self.dynamo_server.manager.master

        try:
            # authorize the user
            user_cert_data = conn.getpeercert()

            for dkey in ['subject', 'issuer']:
                dn = ''
                for rdn in user_cert_data['subject']:
                    dn += '/' + '+'.join('%s=%s' % (DN_TRANSLATION[key], value) for key, value in rdn)
   
                user_name = master.identify_user(dn)
                if user_name is not None:
                    break
            else:
                io.send('failed', 'Unidentified user DN %s' % dn)
                return

            app_data = io.recv()
    
            if not master.authorize_user(user_name, app_data['service']):
                io.send('failed', 'Unauthorized user/service %s/%s' % (user_name, app_data['service']))
                return

            command = app_data['command']

            if command == 'poll' or command == 'kill':
                self._act_on_app(command, app_data['appid'], io)
                return

            # new application - get the work area path
            if 'path' in app_data:
                # work area specified
                workarea = app_data['path']
            else:
                workarea = self._make_workarea()
                if not workarea:
                    io.send('failed', 'Failed to create work area')

            if command == 'submit':
                self._submit_app(workarea, app_data, io)

            elif command == 'interact':
                self._interact(workarea, io)

                # cleanup
                if 'path' not in app_data:
                    shutil.rmtree(workarea)

        except:
            io.send('failed', 'Exception: ' + str(sys.exc_info()[1]))
        finally:
            conn.close()

    def _act_on_app(self, command, app_id, io):
        # query or operation on existing application

        master = self.dynamo_server.manager.master

        apps = master.get_applications(app_id = app_id)
        if len(apps) == 0:
            io.send('failed', 'Unknown appid %d' % app_id)
            return

        app = apps[0]

        if command == 'kill':
            if app['status'] == ServerManager.APP_NEW or app['status'] == ServerManager.APP_RUN:
                master.update_application(app_id, status = ServerManager.APP_KILLED)
                io.send('OK', 'Task aborted.')
            else:
                io.send('OK', 'Task already completed with status %s (exit code %s).' % \
                    (ServerManager.application_status_name(app['status']), app['exit_code']))
        else:
            app['status'] = ServerManager.application_status_name(app['status'])
            io.send('OK', app)

    def _submit_app(self, app_data, workarea, io):
        # schedule the app on master
        for key in ['title', 'args', 'write_request']:
            if key not in app_data:
                io.send('failed', 'Missing ' + key)
                return

        if 'exec_path' in app_data:
            try:
                shutil.copyfile(app_data['exec_path'], workarea + '/exec.py')
            except:
                io.send('failed', 'Could not copy %s' % workarea)
                return

            app_data.pop('exec_path')

        elif 'exec' in app_data:
            with open(workarea + '/exec.py', 'w') as out:
                out.write(app_data['exec'])
                
            app_data.pop('exec')

        app_data['path'] = workarea
        app_data['user'] = user

        mode = app_data.pop('mode')

        self._schedule_app(mode, **app_data)

        if mode == 'synch':
            msg = self.wait_synch_app_queue(app_id)

            if msg['status'] != ServerManager.APP_RUN:
                # this app is not going to run
                io.send('failed', {'status': ServerManager.application_status_name(msg['status'])})
                return

            io.send('OK', {'appid': app_id, 'path': msg['path']}) # msg['path'] should be == workarea

            # synchronous execution = client watches the app run
            # client sends the socket address to connect stdout/err to
            addr = io.recv()

            result = self._serve_synch_app(app_id, msg['pid'], addr)

            io.send('OK', result)

        else:
            io.send('OK', {'appid': app_id, 'path': workarea})

    def _interact(self, workarea, io):
        io.send('OK')
        
        addr = io.recv()
        oconn = socket.socket(socket.AF_INET)
        oconn.connect((addr['host'], addr['port']))
        econn = socket.socket(socket.AF_INET)
        econn.connect((addr['host'], addr['port']))

        proc = multiprocessing.Process(target = self._run_interactive, name = 'interactive', (workarea, oconn, econn))
        proc.start()
        # oconn and econn file descriptors are duplicated in the subprocess. Close mine.
        oconn.close()
        econn.close()

        proc.join()

    def _run_interactive(self, workarea, oconn, econn):
        stdout = oconn.makefile('w')
        stderr = econn.makefile('w')

        make_console = lambda l: SocketDynamoConsole(oconn, l)

        self.dynamo_server.run_interactive(workarea, stdout, stderr, make_console)

        oconn.shutdown(socket.SHUT_RDWR)
        oconn.close()
        econn.shutdown(socket.SHUT_RDWR)
        econn.close()

    def _serve_synch_app(self, app_id, pid, addr):
        conns = (socket.socket(socket.AF_INET), socket.socket(socket.AF_INET))

        stop_reading = threading.Event()

        for conn, name in zip(conns, ('stdout', 'stderr')):
            conn.connect((addr['host'], addr['port']))
            args = (path + '/_' + name, conn, stop_reading)
            th = threading.Thread(target = tail_follow, name = name, args = args)
            th.daemon = True
            th.start()

        os.waitpid(pid, 0)

        stop_reading.set()

        for conn in conns:
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()

        active_status = (ServerManager.APP_NEW, ServerManager.APP_ASSIGNED, ServerManager.APP_RUN)

        while True:
            apps = self.dynamo_server.manager.master.get_applications(app_id = app_id)
            if len(apps) == 0:
                # application disappeared from master DB!?
                return {'status': 'unknown', 'exit_code': None}
            else:
                app = apps[0]
                if app['status'] in active_status:
                    # master server hasn't been updated yet
                    time.sleep(1)
                else:
                    return {'status': ServerManager.application_status_name(app['status']), 'exit_code': app['exit_code']}
