#!/usr/bin/env python3

import os
import sys
import pytz
import time
import argparse
import calendar
import threading
from datetime import datetime, timedelta

import jinja2
import cherrypy
from cherrypy.process.plugins import Daemonizer

import logging
from logging.handlers import WatchedFileHandler

from config import *
from database import Archive
from scheduler import ScheduleProcessor
from weather import get_current_temperature, get_daily_et


# Path configuration
_BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(_BASE_PATH, 'css')
JS_PATH = os.path.join(_BASE_PATH, 'js')
TEMPLATE_PATH = os.path.join(_BASE_PATH, 'templates')


# Timezone configuration
_LOCAL_TZ = pytz.utc
if os.path.exists('/etc/timezone'):
    with open('/etc/timezone', 'r') as fh:
        _LOCAL_TZ = pytz.timezone(fh.read().strip().rstrip())


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
    si = open(stdin, 'r')
    so = open(stdout, 'a+')
    se = open(stderr, 'a+')
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())


# AJAX interface
class AJAX(object):
    def __init__(self, config, hardwareZones, history, scheduler):
        self.config = config
        self.hardwareZones = hardwareZones
        self.history = history
        self.scheduler = scheduler
        
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
        output['history'] = 'active' if self.history.is_alive() else 'failed'
        output['scheduler'] = 'active' if self.scheduler.is_alive() else 'failed'
        
        output['zones'] = []
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            output['status%i' % i] = 'on' if zone.is_active else 'off'
            output['etv%i' % i] = zone.current_et_value
            output['name%i' % i] = self.config.get('Zone%i' % i, 'name')
            output['zones'].append(i)
        for entry in self.history.get_data():
            try:
                output['start%i' % entry['zone']]
                output['run%i' % entry['zone']]
                output['adjust%i' % entry['zone']]
            except KeyError:
                lStart = datetime.utcfromtimestamp(entry['dateTimeStart'])
                if entry['dateTimeStop'] > 0:
                    lStop = datetime.utcfromtimestamp(entry['dateTimeStop'])
                else:
                    lStop = datetime.utcnow()
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
            output['status'] = 'on' if self.hardwareZones[id-1].is_active else 'off'
            for entry in self.history.get_data():
                if entry['zone'] == id:
                    output['lastStart'] = self.serialize(datetime.utcfromtimestamp(entry['dateTimeStart']))
                    output['lastStop'] = self.serialize(datetime.utcfromtimestamp(entry['dateTimeStop']))
                    output['adjust'] = entry['wxAdjust']
                    
        except Exception as e:
            print(str(e))
            
        return output
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def control(self, **kwds):
        if len(kwds.keys()) > 0:
            for keyword,value in kwds.items():
                if keyword[:4] == 'zone' and keyword.find('-') == -1:
                    i = int(keyword[4:])
                    if value == 'on' and not self.hardwareZones[i-1].is_active:
                        self.hardwareZones[i-1].on()
                        self.history.write_data(time.time(), i, 'on', wx_adjustment=-1.0)
                    if value == 'off' and self.hardwareZones[i-1].is_active:
                        self.hardwareZones[i-1].off()
                        self.history.write_data(time.time(), i, 'off')
                                    
        output = {}
        output['zones'] = []
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            output['status%i' % i] = 'on' if zone.is_active else 'off'
            output['name%i' % i] = self.config.get('Zone%i' % i, 'name')
            output['zones'].append(i)
            
        return output
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def log(self):
        output = {}
        
        tNow = datetime.utcnow()
        history = self.history.get_data(age=14*24*3600)[:25]
        
        output['tNow'] = self.serialize(tNow)
        output['entries'] = []
        for i,entry in enumerate(history):
            i += 1
            output['entry%iZone' % i] = entry['zone']
            output['entry%iStart' % i] = pytz.utc.localize(datetime.utcfromtimestamp(entry['dateTimeStart'])).astimezone(_LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
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
    def __init__(self, config, hardwareZones, history, scheduler):
        self.config = config
        self.hardwareZones = hardwareZones
        self.history = history
        self.scheduler = scheduler
        
        self.query = AJAX(config, hardwareZones, history, scheduler)
        
    @cherrypy.expose
    def index(self):
        kwds = self.config.dict
        kwds['tNow'] = datetime.now()
        kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
        
        kwds['history'] = 'active' if self.history.is_alive() else 'failed'
        kwds['scheduler'] = 'active' if self.scheduler.is_alive() else 'failed'
        
        for i,zone in enumerate(self.hardwareZones):
            i += 1
            kwds['zone%i-status' % i] = 'on' if zone.is_active else 'off'
            kwds['zone%i-current_et_value' % i] = zone.current_et_value
        for entry in self.history.get_data():
            try:
                kwds['zone%i-lastStart' % entry['zone']]
                kwds['zone%i-lastStop' % entry['zone']]
                kwds['zone%i-adjust' % entry['zone']]
            except KeyError:
                kwds['zone%i-lastStart' % entry['zone']] = pytz.utc.localize(datetime.utcfromtimestamp(entry['dateTimeStart'])).astimezone(_LOCAL_TZ)
                kwds['zone%i-lastStop' % entry['zone']] = pytz.utc.localize(datetime.utcfromtimestamp(entry['dateTimeStop'])).astimezone(_LOCAL_TZ)
                kwds['zone%i-adjust' % entry['zone']] = entry['wxAdjust']
                
        template = jinjaEnv.get_template('index.html')
        return template.render({'kwds':kwds})
        
    @cherrypy.expose
    def zones(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.dict
        else:
            self.config.dict = kwds
            save_config(CONFIG_FILE, self.config)
            
        template = jinjaEnv.get_template('zones.html')
        return template.render({'kwds':kwds})
    
    @cherrypy.expose
    def schedules(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.dict
        else:
            self.config.dict = kwds
            save_config(CONFIG_FILE, self.config)
        kwds['tNow'] = datetime.now()
        
        mname = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 
                 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}
                 
        template = jinjaEnv.get_template('schedules.html')
        return template.render({'kwds':kwds, 'mname':mname})
    
    @cherrypy.expose
    def weather(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.dict
        else:
            self.config.dict = kwds
            save_config(CONFIG_FILE, self.config)
            
        if 'test-config' in kwds.keys():
            if kwds['weather-pws'] == '':
                kwds['weather-info'] = 'Error: No PWS ID provided'
            else:
                kwds['weather-info'] = "Current temperature: %.0f F" % (get_current_temperature(kwds['weather-pws']),)
                kwds['weather-info'] += "<br />Current daily ET loss: %.2f inches" % (get_daily_et(kwds['weather-pws'],
                                                                                                   Kc=float(kwds['weather-kc']),
                                                                                                   Cn=float(kwds['weather-cn']),
                                                                                                   Cd=float(kwds['weather-cd'])),)
                
        else:
            kwds['weather-info'] = ''
            
        template = jinjaEnv.get_template('weather.html')
        return template.render({'kwds':kwds})
    
    @cherrypy.expose
    def manual(self, **kwds):
        if len(kwds) == 0:
            kwds = self.config.dict
        else:
            configDict = self.config.dict
            for keyword,value in configDict.items():
                if keyword not in kwds.keys():
                    kwds[keyword] = value
                    
        for keyword,value in kwds.items():
            if keyword[:4] == 'zone' and keyword.find('-') == -1:
                i = int(keyword[4:])
                if value == 'on' and not self.hardwareZones[i-1].is_active:
                    self.hardwareZones[i-1].on()
                    self.history.write_data(time.time(), i, 'on', wx_adjustment=-1.0)
                if value == 'off' and self.hardwareZones[i-1].is_active:
                    self.hardwareZones[i-1].off()
                    self.history.write_data(time.time(), i, 'off')
                    
        kwds['manual-info'] = ''
        for i,zone in enumerate(self.hardwareZones):
            i = i + 1
            if kwds['zone%i-enabled' % i] == 'on':
                if zone.is_active:
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
        kwds['tNow'] = _LOCAL_TZ.localize(datetime.now())
        kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
        kwds['history'] = self.history.get_data(age=7*24*3600)[:25]
        for i in range(len(kwds['history'])):
            kwds['history'][i]['dateTimeStart'] = pytz.utc.localize(datetime.utcfromtimestamp(kwds['history'][i]['dateTimeStart'])).astimezone(_LOCAL_TZ)
            kwds['history'][i]['dateTimeStop'] = pytz.utc.localize(datetime.utcfromtimestamp(kwds['history'][i]['dateTimeStop'])).astimezone(_LOCAL_TZ)
            
        template = jinjaEnv.get_template('log.html')
        return template.render({'kwds':kwds})


def main(args):
    # Setup logging
    logger = logging.getLogger(__name__)
    logFormat = logging.Formatter('%(asctime)s [%(levelname)-8s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logFormat.converter = time.gmtime
    if args.log_file == 'stdout':
        logHandler = logging.StreamHandler()
    else:
        logHandler = WatchedFileHandler(args.log_file)
    logHandler.setFormatter(logFormat)
    logger.addHandler(logHandler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        
    # PID file
    if args.pid_file is not None:
        with open(args.pid_file, 'w') as fh:
            fh.write("%i\n" % os.getpid())
            
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
    config = load_config(args.config_file)
    
    # Initialize the archive
    history = Archive(config)
    history.start()
    
    # Initialize the hardware
    hardwareZones = init_zones(config)
    for previousRun in history.get_data(scheduled_only=True):
        logger.info('Previous run of zone %i was on %s UTC', previousRun['zone'], datetime.utcfromtimestamp(previousRun['dateTimeStart']))
        logger.info('Previous ET value of zone %i was %.2f inches', previousRun['zone'], hardwareZones[previousRun['zone']-1].current_et_value)
        
        if hardwareZones[previousRun['zone']-1].lastStart == 0:
            hardwareZones[previousRun['zone']-1].lastStart = previousRun['dateTimeStart']
            hardwareZones[previousRun['zone']-1].lastStop = previousRun['dateTimeStop']
            
    # Initialize the scheduler
    scheduler = ScheduleProcessor(config, hardwareZones, history)
    scheduler.start()
    
    # Initialize the web interface
    ws = Interface(config, hardwareZones, history, scheduler)
    #cherrypy.quickstart(ws, config=cpConfig)
    cherrypy.engine.signal_handler.subscribe()
    cherrypy.tree.mount(ws, "/", config=cpConfig)
    cherrypy.engine.start()
    cherrypy.engine.block()
    
    # Shutdown process
    logger.info('Shutting down Pi2O, please wait...')
    
    # Stop the scheduler thread
    scheduler.cancel()
    
    # Make sure the sprinkler zones are off
    for i,zone in enumerate(hardwareZones):
        if zone.is_active:
            zone.off()
            history.write_data(time.time(), i, 'off')
        ## Save the ET values so that we have some state
        config.set('Zone%i' % (i+1), 'current_et_value', "%.2f" % zone.current_et_value)
        logger.info('Saved ET value for zone %i of %.2f inches', i+1, zone.current_et_value)
        
    # Shutdown the archive
    history.cancel()
    
    # Save the final configuration
    save_config(args.config_file, config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
            description="control your sprinklers with a Raspberry Pi",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
            )
    parser.add_argument('-c', '--config-file', type=str, default=CONFIG_FILE,
                        help='configuraton file to use')
    parser.add_argument('-p', '--pid-file', type=str,
                        help='file to write the current PID to')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='set the logging to \'debug\' level')
    parser.add_argument('-l', '--log-file', type=str, default='/var/log/pi2o',
                        help='set the logfile')
    parser.add_argument('-f', '--foreground', action='store_true',
                        help='run in the foreground instead of daemonizing')
    args = parser.parse_args()
    
    if not args.foreground:
        for redirname in ('/tmp/Pi2O.stdout', '/tmp/Pi2O.stderr'):
            try:
                os.unlink(redirname+'_old')
            except OSError:
                pass
            try:
                os.rename(redirname, redirname+'_old')
            except OSError:
                pass
        daemonize('/dev/null', '/tmp/Pi2O.stdout', '/tmp/Pi2O.stderr')
    main(args)
