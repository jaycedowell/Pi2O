#!/usr/bin/env python

import os
import sys
import time
import getopt
import calendar
import threading
from datetime import datetime, timedelta

import jinja2
import cherrypy
from cherrypy.process.plugins import Daemonizer

import logging
try:
    from logging.handlers import WatchedFileHandler
except ImportError:
    from logging import FileHandler as WatchedFileHandler
    
from config import *
from database import Archive
from scheduler import ScheduleProcessor
from weather import get_current_temperature, get_daily_et


# Path configuration
_BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(_BASE_PATH, 'css')
JS_PATH = os.path.join(_BASE_PATH, 'js')
TEMPLATE_PATH = os.path.join(_BASE_PATH, 'templates')


# Jinja configuration
jinjaEnv = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_PATH), 
                              extensions=['jinja2.ext.loopcontrols',])


"""
This module is used to fork the current process into a daemon.
Almost none of this is necessary (or advisable) if your daemon
is being started by inetd. In that case, stdin, stdout and stderr are
all set up for you to refer to the network connection, and the fork()s
and session manipulation should not be done (to avoid confusing inetd).
Only the chdir() and umask() steps remain as useful.

From:
  http://code.activestate.com/recipes/66012-fork-a-daemon-process-on-unix/

References:
  UNIX Programming FAQ
    1.7 How do I get my program to act like a daemon?
        http://www.erlenstar.demon.co.uk/unix/faq_2.html#SEC16
        
    Advanced Programming in the Unix Environment
      W. Richard Stevens, 1992, Addison-Wesley, ISBN 0-201-56317-7.
"""

def daemonize(stdin='/dev/null', stdout='/dev/null', stderr='/dev/null'):
    """
    This forks the current process into a daemon.
    The stdin, stdout, and stderr arguments are file names that
    will be opened and be used to replace the standard file descriptors
    in sys.stdin, sys.stdout, and sys.stderr.
    These arguments are optional and default to /dev/null.
    Note that stderr is opened unbuffered, so
    if it shares a file with stdout then interleaved output
    may not appear in the order that you expect.
    """
    
    # Do first fork.
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0) # Exit first parent.
    except OSError as e:
        sys.stderr.write("fork #1 failed: (%d) %s\n" % (e.errno, e.strerror))
        sys.exit(1)
        
    # Decouple from parent environment.
    os.chdir("/")
    os.umask(0)
    os.setsid()
    
    # Do second fork.
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0) # Exit second parent.
    except OSError as e:
        sys.stderr.write("fork #2 failed: (%d) %s\n" % (e.errno, e.strerror))
        sys.exit(1)
        
    # Now I am a daemon!
    
    # Redirect standard file descriptors.
    si = file(stdin, 'r')
    so = file(stdout, 'a+')
    se = file(stderr, 'a+', 0)
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())


def usage(exitCode=None):
    print """Pi2O.py - Control your sprinklers with a Raspberry Pi

Usage: Pi2O.py [OPTIONS]

Options:
-h, --help                  Display this help information
-p, --pid-file              File to write the current PID to
-d, --debug                 Set the logging to 'debug' level
-l, --logfile               Set the logfile (default = /var/log/pi2o)
"""

    if exitCode is not None:
        sys.exit(exitCode)
    else:
        return True


def parseOptions(args):
    config = {}
    config['configFile'] = CONFIG_FILE
    config['pidFile'] = None
    config['debug'] = False
    config['logfile'] = '/var/log/pi2o'

    try:
        opts, args = getopt.getopt(args, "hp:dl:", ["help", "pid-file=", "debug", "logfile="])
    except getopt.GetoptError as err:
        # Print help information and exit:
        print str(err) # will print something like "option -a not recognized"
        usage(exitCode=2)
    
    # Work through opts
    for opt, value in opts:
        if opt in ('-h', '--help'):
            usage(exitCode=0)
        elif opt in ('-p', '--pid-file'):
            config['pidFile'] = str(value)
        elif opt in ('-d', '--debug'):
            config['debug'] = True
        elif opt in ('-l', '--logfile'):
            config['logfile'] = value
        else:
            assert False
    
    # Add in arguments
    config['args'] = args

    # Return configuration
    return config


# AJAX interface
class AJAX(object):
    def __init__(self, config, hardwareZones, history):
        self.config = config
        self.hardwareZones = hardwareZones
        self.history = history
        
    def serialize(self, dt):
        if isinstance(dt, datetime):
            if dt.utcoffset() is not None:
                dt = dt - dt.utcoffset()
        millis = int(calendar.timegm(dt.timetuple()) * 1000 + dt.microsecond / 1000)
        return millis
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def summary(self):
        output = {}

        output['zones'] = []
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            output['status%i' % i] = 'on' if zone.is_active() else 'off'
            output['etv%i' % i] = zone.current_et_value
            output['name%i' % i] = self.config.get('Zone%i' % i, 'name')
            output['zones'].append(i)
        for entry in self.history.get_data():
            try:
                output['start%i' % entry['zone']]
                output['run%i' % entry['zone']]
                output['adjust%i' % entry['zone']]
            except KeyError:
                lStart = datetime.fromtimestamp(entry['dateTimeStart'])
                if entry['dateTimeStop'] > 0:
                    lStop = datetime.fromtimestamp(entry['dateTimeStop'])
                else:
                    lStop = datetime.now()
                output['start%i' % entry['zone']] = self.serialize(lStart)
                output['run%i' % entry['zone']] = self.serialize(lStop)-self.serialize(lStart)
                output['adjust%i' % entry['zone']] = entry['wxAdjust']
                
        return output
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def zone(self, id):
        output = {}
        
        try:
            id = int(id)
            output['status'] = 'on' if self.hardwareZones[id-1].is_active() else 'off'
            for entry in self.history.get_data():
                if entry['zone'] == id:
                    output['lastStart'] = self.serialize(datetime.fromtimestamp(entry['dateTimeStart']))
                    output['lastStop'] = self.serialize(datetime.fromtimestamp(entry['dateTimeStop']))
                    output['adjust'] = entry['wxAdjust']
                    
        except Exception as e:
            print str(e)
            
        return output
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def control(self, **kwds):
        if len(kwds.keys()) > 0:
            for keyword,value in kwds.iteritems():
                if keyword[:4] == 'zone' and keyword.find('-') == -1:
                    i = int(keyword[4:])
                    if value == 'on' and not self.hardwareZones[i-1].is_active():
                        self.hardwareZones[i-1].on()
                        self.history.write_data(time.time(), i, 'on', wx_adjustment=-1.0)
                    if value == 'off' and self.hardwareZones[i-1].is_active():
                        self.hardwareZones[i-1].off()
                        self.history.write_data(time.time(), i, 'off')
                                    
        output = {}
        output['zones'] = []
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            output['status%i' % i] = 'on' if zone.is_active() else 'off'
            output['name%i' % i] = self.config.get('Zone%i' % i, 'name')
            output['zones'].append(i)
            
        return output
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def log(self):
        output = {}
        
        tNow = datetime.now()
        history = self.history.get_data(age=14*24*3600)[:25]
        
        output['tNow'] = self.serialize(tNow)
        output['entries'] = []
        for i,entry in enumerate(history):
            i += 1
            output['entry%iZone' % i] = entry['zone']
            output['entry%iStart' % i] = datetime.fromtimestamp(entry['dateTimeStart']).strftime("%Y-%m-%d %H:%M:%S")
            if entry['dateTimeStop'] >= entry['dateTimeStart']:
                active = False
                runtime = entry['dateTimeStop'] - entry['dateTimeStart']
            else:
                active = True
                runtime = time.time() - entry['dateTimeStart']
            output['entry%iRun' % i] = "%i:%02i:%02i%s" % (runtime/3600, runtime%3600/60, runtime%60, " <i>(running)</i>" if active else "")
            if entry['wxAdjust'] >= 0:
                output['entry%iAdjust' % i] = "%i%%" % (100.0*entry['wxAdjust'],)
            elif entry['wxAdjust'] == -1:
                output['entry%iAdjust' % i] = 'Manual'
            else:
                output['entry%iAdjust' % i] = 'Disabled'
            output['entries'].append(i)
            
        return output


# Main web interface
class Interface(object):
    def __init__(self, config, hardwareZones, history):
        self.config = config
        self.hardwareZones = hardwareZones
        self.history = history
        
        self.query = AJAX(config, hardwareZones, history)
        
    @cherrypy.expose
    def index(self):
        kwds = self.config.as_dict()
        kwds['tNow'] = datetime.now()
        kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            kwds['zone%i-status' % i] = 'on' if zone.is_active() else 'off'
            kwds['zone%i-current_et_value' % i] = zone.current_et_value
        for entry in self.history.get_data():
            try:
                kwds['zone%i-lastStart' % entry['zone']]
                kwds['zone%i-lastStop' % entry['zone']]
                kwds['zone%i-adjust' % entry['zone']]
            except KeyError:
                kwds['zone%i-lastStart' % entry['zone']] = datetime.fromtimestamp(entry['dateTimeStart'])
                kwds['zone%i-lastStop' % entry['zone']] = datetime.fromtimestamp(entry['dateTimeStop'])
                kwds['zone%i-adjust' % entry['zone']] = entry['wxAdjust']
                
        template = jinjaEnv.get_template('index.html')
        return template.render({'kwds':kwds})
        
    @cherrypy.expose
    def zones(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.as_dict()
        else:
            self.config.from_dict(kwds)
            save_config(CONFIG_FILE, self.config)
            
        template = jinjaEnv.get_template('zones.html')
        return template.render({'kwds':kwds})
    
    @cherrypy.expose
    def schedules(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.as_dict()
        else:
            self.config.from_dict(kwds)
            save_config(CONFIG_FILE, self.config)
        kwds['tNow'] = datetime.now()
        
        mname = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 
                 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}
                 
        template = jinjaEnv.get_template('schedules.html')
        return template.render({'kwds':kwds, 'mname':mname})
    
    @cherrypy.expose
    def weather(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.as_dict()
        else:
            self.config.from_dict(kwds)
            save_config(CONFIG_FILE, self.config)
            
        if 'test-config' in kwds.keys():
            if kwds['weather-pws'] == '':
                kwds['weather-info'] = 'Error: No PWS ID provided'
            else:
                kwds['weather-info'] = "Current temperature: %.0f F" % (get_current_temperature(kwds['weather-pws']),)
                kwds['weather-info'] += "<br />Current daily ET loss: %.2f inches" % (get_daily_et(kwds['weather-pws'], 
                                                                                              Cn=float(kwds['weather-cn']), 
                                                                                              Cd=float(kwds['weather-cd'])),)
                
        else:
            kwds['weather-info'] = ''
            
        template = jinjaEnv.get_template('weather.html')
        return template.render({'kwds':kwds})
    
    @cherrypy.expose
    def manual(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.as_dict()
        else:
            configDict = self.config.as_dict()
            for keyword,value in configDict.iteritems():
                if keyword not in kwds.keys():
                    kwds[keyword] = value
                    
        for keyword,value in kwds.iteritems():
            if keyword[:4] == 'zone' and keyword.find('-') == -1:
                i = int(keyword[4:])
                if value == 'on' and not self.hardwareZones[i-1].is_active():
                    self.hardwareZones[i-1].on()
                    self.history.write_data(time.time(), i, 'on', wx_adjustment=-1.0)
                if value == 'off' and self.hardwareZones[i-1].is_active():
                    self.hardwareZones[i-1].off()
                    self.history.write_data(time.time(), i, 'off')
                    
        kwds['manual-info'] = ''
        for i,zone in enumerate(self.hardwareZones):
            i = i + 1
            if kwds['zone%i-enabled' % i] == 'on':
                if zone.is_active():
                    kwds['zone%i' % i] = 'selected'
                    kwds['manual-info'] += 'Zone %i is turned on<br />' % i
                else:
                    kwds['zone%i' % i] = ''
                    kwds['manual-info'] += 'Zone %i is turned off<br />' % i
                
        template = jinjaEnv.get_template('manual.html')
        return template.render({'kwds':kwds})
        
    @cherrypy.expose
    def logs(self, **kwds):
        kwds = {}
        kwds['tNow'] = datetime.now()
        kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
        kwds['history'] = self.history.get_data(age=7*24*3600)[:25]
        for i in xrange(len(kwds['history'])):
            kwds['history'][i]['dateTimeStart'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStart'])
            kwds['history'][i]['dateTimeStop'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStop'])
            
        template = jinjaEnv.get_template('log.html')
        return template.render({'kwds':kwds})


def main(args):
    # Parse the command line and read in the configuration file
    cmdConfig = parseOptions(args)
    
    # Setup logging
    logger = logging.getLogger(__name__)
    logFormat = logging.Formatter('%(asctime)s [%(levelname)-8s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logFormat.converter = time.gmtime
    logHandler = WatchedFileHandler(cmdConfig['logfile'])
    logHandler.setFormatter(logFormat)
    logger.addHandler(logHandler)
    if cmdConfig['debug']:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        
    # PID file
    if cmdConfig['pidFile'] is not None:
        fh = open(cmdConfig['pidFile'], 'w')
        fh.write("%i\n" % os.getpid())
        fh.close()
        
    # CherryPy configuration
    cherrypy.config.update({'server.socket_host': '0.0.0.0', 'server.socket_port': 80, 'environment': 'production'})
    cpConfig = {'/css': {'tools.staticdir.on': True,
                         'tools.staticdir.dir': CSS_PATH},
                  '/js':  {'tools.staticdir.on': True,
                           'tools.staticdir.dir': JS_PATH}
                  }
                
    # Report on who we are
    logger.info('Starting Pi2O.py with PID %i', os.getpid())
    logger.info('All dates and times are in UTC except where noted')
     
    # Load in the configuration
    config = load_config(cmdConfig['configFile'])
    
    # Initialize the archive
    history = Archive(config)
    history.start()
    
    # Initialize the hardware
    hardwareZones = init_zones(config)
    for previousRun in history.get_data(scheduled_only=True):
        logger.info('Previous run of zone %i was on %s LT', previousRun['zone'], datetime.fromtimestamp(previousRun['dateTimeStart']))
        logger.info('Previous ET value of zone %i was %.2f inches', previousRun['zone'], hardwareZones[previousRun['zone']-1].current_et_value)
        
        if hardwareZones[previousRun['zone']-1].lastStart == 0:
            hardwareZones[previousRun['zone']-1].lastStart = previousRun['dateTimeStart']
            hardwareZones[previousRun['zone']-1].lastStop = previousRun['dateTimeStop']
            
    # Initialize the scheduler
    bg = ScheduleProcessor(config, hardwareZones, history)
    bg.start()
    
    # Initialize the web interface
    ws = Interface(config, hardwareZones, history)
    #cherrypy.quickstart(ws, config=cpConfig)
    cherrypy.engine.signal_handler.subscribe()
    cherrypy.tree.mount(ws, "/", config=cpConfig)
    cherrypy.engine.start()
    cherrypy.engine.block()
    
    # Shutdown process
    logger.info('Shutting down Pi2O, please wait...')
    
    # Stop the scheduler thread
    bg.cancel()
    
    # Make sure the sprinkler zones are off
    for i,zone in enumerate(hardwareZones):
        if zone.is_active():
            zone.off()
            history.write_data(time.time(), i, 'off')
        ## Save the ET values so that we have some state
        config.set('Zone%i' % (i+1), 'current_et_value', "%.2f" % zone.current_et_value)
        logger.info('Saved ET value for zone %i of %.2f inches', i+1, zone.current_et_value)
        
    # Shutdown the archive
    history.cancel()
    
    # Save the final configuration
    save_config(cmdConfig['configFile'], config)
    logger.info('Done')


if __name__ == "__main__":
    try:
        os.unlink('/tmp/Pi2O.stdout')
    except OSError:
        pass
    try:
        os.unlink('/tmp/Pi2O.stderr')
    except OSError:
        pass
        
    daemonize('/dev/null', '/tmp/Pi2O.stdout', '/tmp/Pi2O.stderr')
    main(sys.argv[1:])
    
