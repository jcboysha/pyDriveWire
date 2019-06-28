#!/usr/local/bin/python
import serial
from dwserial import DWSerial
from dwsocket import DWSocketServer, DWSocket, DWSimpleSocket
from dwserver import DWServer
from dwcommand import DWRepl, DWRemoteRepl, DWParser
from dwhttpserver import DWHttpServer
import traceback
import logging
import argparse
from argparse import Namespace

import sys
import os
import time
import threading
import atexit

from daemon import Daemon
import platform

VERSION = 'v0.5a'

def ParseArgs():
    parser = argparse.ArgumentParser(description='pyDriveWire Server %s' % VERSION)
    parser.add_argument('-s', '--speed', dest='speed', help='Serial port speed')
    parser.add_argument('-a', '--accept', dest='accept', action='store_true', help='Accept incoming TCP connections on --port')
    parser.add_argument('-c', '--connect', dest='connect', action='store_true', help='Connect to TCP connections --host --port')
    parser.add_argument('-H', '--host', dest='host', help='Hostname/IP')
    parser.add_argument('-p', '--port', dest='port', help='Port to use')
    parser.add_argument('-R', '--rtscts', dest='rtscts', action='store_true', help='Serial: Enable RTS/CTS Flow Control')
    parser.add_argument('-x', dest='experimental', action='append', help='experimental options')
    parser.add_argument('-D', '--cmd-port', dest='cmdPort', help='Remote dw command input')
    parser.add_argument('-U', '--ui-port', dest='uiPort', help='pyDriveWire UI Port')
    parser.add_argument('-C', '--config', dest='config', help='Config File', default="~/.pydrivewirerc")
    parser.add_argument('--daemon', dest='daemon', action='store_true', help='Daemon Mode, No Repl')
    parser.add_argument('--status', dest='daemonStatus', action='store_true', help='Daemon Status')
    parser.add_argument('--stop', dest='daemonStop', action='store_true', help='Daemon Status')
    parser.add_argument('--pid-file', dest='daemonPidFile', help='Daemon Pid File')
    parser.add_argument('--log-file', dest='daemonLogFile', help='Daemon Log File')
    parser.add_argument('--debug', '-d', dest='debug', action='count')
    parser.add_argument('--version', '-v', action='store_true')
    parser.add_argument('--hdbdos', dest='hdbdos', action='store_true', help='HDBDos Mode')
    parser.add_argument('--offset', dest='offset', help='Number of sector offset for sector 0', default='0')
    parser.add_argument('--noreconnect', dest='noreconnect', action='store_true', help='Do not automatically reconnect outbound TCP server connections')
    printer = parser.add_argument_group('printer', 'Printer Options')
    printer.add_argument('--print-format', dest='printFormat', choices=['pdf', 'txt'], help='Printer output format, default: %(default)s', default="pdf") 
    #printer.add_argument('--print-mode', dest='printMode', choices=['dir', 'file'], help='Printer output collation method, default: %(default)s', default="dir") 
    printerLoc = printer.add_mutually_exclusive_group()
    printerLoc.add_argument('--print-dir', dest='printDir', help='Spool directory to send printer output')
    printerLoc.add_argument('--print-file', dest='printFile', help='File to send printer output, Note: Will be overwritten')
    printer.add_argument('--print-cmd', dest='printCmd', help='Command to run on flushed printer output')

    parser.add_argument('files', metavar='FILE', nargs='*',
                    help='list of files')

    args = parser.parse_args()
    daemonAct = any([args.daemonStatus, args.daemonStop])

    err = _processMutuallyExclusiveArgs(args)
    if not err:
        args = ReadConfig(args)
        #print args
        #err = None
        err = _processMutuallyExclusiveArgs(args)
    if err:
        pass
    elif not any([args.port, args.accept, args.connect, args.daemon, args.daemonStatus, args.daemonStop]):
        err = "Must supply one of --port, --accept, or --connect or config file"
    elif any([args.daemon, args.daemonStatus, args.daemonStop]) and platform.system() in ['Windows']:
        err = "Daemon mode not supported on %s" % platform.system()
    elif args.daemon and not args.uiPort and not args.cmdPort:
        err = "Daemon mode must have a user interface port, --ui-port"
    elif args.daemon and (not args.daemonPidFile or not args.daemonLogFile):
        err = "Daemon mode must specify --pid-file and --log-file"
    elif args.daemonStop and not args.daemonPidFile:
        err = "Daemon stop: Must specify --pid-file"
    elif args.daemonStatus and not args.daemonPidFile:
        err = "Daemon status:  Must specify --pid-file"
    elif daemonAct:
        pass
    elif args.accept and not args.port:
        err = "TCP Accept must supply --accept and --port"
    elif args.connect and not all([args.port,args.host]):
        err = "TCP Connect must supply --connect, --host, and --port"
    elif not args.accept and not args.connect and not all([args.speed, args.port]):
        err = "Serial connection must supply --speed and --port"

    if err:
        print('\nERROR: %s\n' % err)
        parser.print_usage()
        exit(1)


    #print args
    return args

def _processMutuallyExclusiveArgs(args):
    err = None
    accept, reject = _processMutuallyExclusiveOptions(args)
    # print "pmea", accept, reject
    if any(_getOpts(args, reject)):
        allOpts = set(accept).union(set(reject))
        err = "Mutually exclusive options.  Can't use %s at the same time" % (_getOptNames(args, allOpts))
    return err

def _getOptNames(args, opts):
    r = [o for o in list(opts) if eval('args.%s' % o)]
    # print "getOpts", opts, r
    return ', '.join(r)

def _getOpts(args, opts):
    r = [eval('args.%s' % o) for o in list(opts)]
    # print "getOpts", opts, r
    return r

def _processMutuallyExclusiveOptions(args):
    accept = set()
    reject = set()

    serialOpts = set(['speed'])
    connectOpts = set(['connect', 'host'])
    acceptOpts = set(['accept'])
    daemonActs = set(['daemonStatus', 'daemonStop'])
    daemonOpts = daemonActs.union(set(['daemon']))

    notSerialOpts = connectOpts.union(acceptOpts) - serialOpts
    notConnectOpts = serialOpts.union(acceptOpts) - connectOpts
    notAcceptOpts = serialOpts.union(connectOpts) - acceptOpts

    if sum(_getOpts(args, daemonOpts)) > 1:
        reject = daemonOpts
    #elif any(_getOpts(args, daemonActs)):
    #    reject = list(serialOpts) + list(connectOpts) + list(acceptOpts) + ['port']
    elif any(_getOpts(args, serialOpts)): # and any(_getOpts(args, notSerialOpts)):
        accept = serialOpts
        reject = notSerialOpts
    elif any(_getOpts(args, connectOpts)): # and any(_getOpts(args, notConnectOpts)):
        accept = connectOpts
        reject = notConnectOpts
    elif any(_getOpts(args, acceptOpts)): # and any(_getOpts(args, notAcceptOpts)):
        accept = acceptOpts
        reject = notAcceptOpts
        
    return list(accept), list(reject)
    
def ReadConfig(args):
    instances = []
    args.cmds = []

    i = 0
    instance = 0
    iargs = args
    debug = args.debug

    accept, reject = _processMutuallyExclusiveOptions(args)

    cfgFile = os.path.expanduser(args.config)
    if not os.path.exists(cfgFile):
        args.instances = instances
        return args
    with open(cfgFile) as f:
        for l in f:
            l = l.lstrip().rstrip()
            if l.startswith('#') or l=='':
                continue
            if l.startswith('['):
                instName = l[1:-1]
                instance += 1
                iargs = Namespace()
                iargs.accept = False
                iargs.connect = False
                iargs.host = None
                iargs.port = None
                iargs.speed = None
                iargs.experimental = args.experimental
                iargs.daemon = True
                iargs.rtscts = False
                iargs.files = []
                iargs.cmds = []
                iargs.debug = debug
                iargs.offset = args.offset
                iargs.hdbdos = args.hdbdos
                iargs.noreconnect = args.noreconnect
                iargs.printFormat = args.printFormat
                #iargs.printMode = None
                iargs.printDir = args.printDir
                iargs.printFile = args.printFile
                iargs.printCmd = args.printCmd
                instances.append(iargs)
                continue
            lp = l.split(' ')
            if lp[0].lower() == 'option':
                #args[lp[1]] = lp[2]
                key = lp[1]
                val = lp[2]
                if val in ['True', 'False']:
                        val = eval(val)
                if key == 'debug':
                    val = int(val)
                    if not debug:
                        debug = val

                if instance == 0:
                    # print key, accept, reject
                    if key in reject:
                        print('%d: rejecting line from config file: %s' % (instance, l))
                        continue
                    else:
                        if eval('iargs.%s' % key): 
                            print('%d: rejecting line from config file: %s' % (instance, l))
                        else:
                            exec('iargs.%s = val' % key)
                else:
                    exec('iargs.%s = val' % key)

                #print "%d:option:%s" % (instance,l)
            else:
                if args.debug >= 1 and l.startswith('dw server debug'):
                    continue
                if args.debug == 2 and l.startswith('dw server conn debug'):
                    continue
                iargs.cmds += [l]
                #print "%d:cmd:%s" % (instance,l)
    args.instances = instances
    #args.cmds = cmds
    return args


def CreateServer(args, instance, instances, lock):
	if args.accept:
                print("Accept connection on %s" % args.port)
		conn = DWSocketServer(port=args.port)
	elif args.connect:
                print "Connect to %s:%s" % (args.host,args.port)
		conn = DWSimpleSocket(port=args.port,host=args.host,reconnect=True if not args.noreconnect else False)
		conn.connect()
		conn.run()
	else:
                print "Serial Port: %s at %s, RTS/CTS=%s" % (args.port, args.speed, args.rtscts)
		conn = DWSerial(args.port, args.speed, rtscts=args.rtscts)
		conn.connect()

	dws = DWServer(args, conn, VERSION, instances, instance)
        lock.acquire()
        instances[instance] = dws
        lock.release()


        parser = DWParser(dws)
        cmds = []
        if args.debug >= 1:
            cmds += ['dw server debug 1']
        if args.debug == 2:
            cmds += ['dw server conn debug 1']
        cmds += args.cmds
        for cmd in cmds:
            print parser.parse(cmd)

        return dws

def StartServer(args, dws):
	def cleanup():
		#print "main: Closing serial port."
                for server in dws.instances:
                    server.closeAll()
                    server.conn.cleanup()

        if dws.instance == 0:
            atexit.register(cleanup)

	try:
		drive = 0
		for i,f in enumerate(args.files):
                        if f.lower().startswith('opt='):
                           continue
                        stream = False
                        mode = 'rb+'
                        if i+1 < len(args.files):
                           opt = args.files[i+1].lower()
                           if opt.startswith('opt=')and len(opt) > 4:
                              print "opt=%s" % opt
                              for o in opt[4:].split(','):
                                   print "opt=%s" % o
                                   if o == 'stream':
                                        stream = True
                                   elif o == 'ro':
                                        mode = 'r'
                        print "stream=%s mode=%s" % (stream, mode)
			dws.open(drive, f, mode=mode, stream=stream)
			drive += 1
                if dws.instance == 0:
                    if args.cmdPort:
                            dwe = DWRemoteRepl(dws, args.cmdPort)
                    if args.uiPort:
                            dwhts = DWHttpServer(dws, int(args.uiPort))
                    if not args.daemon:
                        dwr = DWRepl(dws)
		dws.main()
	except:
		traceback.print_exc()
	finally:
		cleanup()


instances = [None]
lock = threading.Lock()
def StartServers(args):
    global instances
    global lock
    instances = [None] * (len(args.instances) + 1)
    threads = []
    #lock = threading.Lock()
    j = 1
    for iargs in args.instances:
        dws = CreateServer(iargs, j, instances, lock)
        instances[j] = dws
        t = threading.Thread(target = StartServer, args=([iargs, dws]))
        t.daemon = True
        threads.append(t)
        j += 1

    dws = CreateServer(args, 0, instances, lock)
    instances[0] = dws

    for i in range(len(instances)):
        instances[i].instances = instances
        instances[i].instance = i

    for t in threads:
        t.start()
    StartServer(args, dws)

class pyDriveWireDaemon(Daemon):
        def run(self):
            StartServers(self.args)

if __name__ == '__main__':
#	logging.basicConfig(stream=sys.stdout, level=logging.INFO,
#		format='%(asctime)s %(levelname)s %(module)s:%(lineno)s.%(funcName)s %(message)s'

    args = ParseArgs()
    if args.version:
        print('pyDriveWire %s' % VERSION)
        exit(0)
    daemon = None
    pid = None
    status = 'notRunning'
    if args.daemon or args.daemonStatus or args.daemonStop:
        pidFile = args.daemonPidFile
        daemon = pyDriveWireDaemon(pidFile, args, stdout=args.daemonLogFile, stderr=args.daemonLogFile)
        if args.daemonStatus or args.daemonStop:
            pid = daemon.getPid()
            if pid:
                status = daemon.getStatus()
    if args.daemonStatus:
            pidMsg = '\b'
            if pid:
                pidMsg  = 'pid:%d' % pid
            print "pyDriveWire Server %s status:%s" % (pidMsg, status)
    elif args.daemonStop:
        msg = ''
        if status == 'Running':
            daemon.stop()
            msg = 'Stopped'
        else:
            msg = status
        print "pyDriveWire Server pid:%s msg:%s" % (pid, msg)
    elif args.daemon:
        daemon.start()
        pid = daemon.getPid()
        status = daemon.getStatus()
        print "pyDriveWire Server %s status:%s" % (pidMsg, status)
        sys.path.stdout.flush()
        sys.exit(0)
    else:
        StartServers(args)

# vim: ts=8 sw=8 sts=8
