# -*- coding: utf-8 -*

"""
Module for processing the sprinkler schedules.
"""

import sys
import time
import logging
import threading
import traceback
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO
from datetime import datetime, timedelta

from weather import get_current_temperature, get_daily_et

__version__ = '0.5'
__all__ = ['ScheduleProcessor', '__version__']


# Logger instance
_LOGGER = logging.getLogger('__main__')


class ScheduleProcessor(threading.Thread):
    """
    Class responsible to running the various zones according to the schedule.
    """

    def __init__(self, config, hardwareZones, history):
        threading.Thread.__init__(self)
        self.interval = 5
        self.config = config
        self.hardwareZones = hardwareZones
        self.history = history
        
        self.thread = None
        self.alive = threading.Event()
        
    def start(self):
        if self.thread is not None:
            self.cancel()
                   
        self.thread = threading.Thread(target=self.run, name='scheduler')
        self.thread.setDaemon(1)
        self.alive.set()
        self.thread.start()
        
        _LOGGER.info('Started the ScheduleProcessor background thread')
        
    def cancel(self):
        if self.thread is not None:
            self.alive.clear()          # clear alive event for thread
            self.thread.join()
            
        _LOGGER.info('Stopped the ScheduleProcessor background thread')
        
    def run(self):
        self.running = True
        self.blockActive = False
        self.updatedET = datetime.now().replace(year=2000)
        self.processedInBlock = []
        
        self.tDelay = timedelta(0)
        
        while self.alive.isSet():
            time.sleep(self.interval)
            if not self.running:
                return True
                
            try:
                tNow = datetime.now()
                tNow = tNow.replace(microsecond=0)
                tNowDB = int(tNow.strftime("%s"))
                _LOGGER.debug('Starting scheduler polling at %s LT', tNow)
                
                # Is the current schedule active?
                if self.config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
                    ## If so, query the start time for this block
                    h,m = [int(i) for i in self.config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
                    s = 0
                    _LOGGER.debug('Current month of %s is enabled with a start time of %i:%02i:%02i LT', tNow.strftime("%B"), h, m, s)
                    
                    ## Update the ET values within one hour of midnight
                    if tNow - tNow.replace(hour=0, minute=0, second=0) < timedelta(hours=1):
                        if tNow - self.updatedET >= timedelta(days=1):
                            ### Load in the WUnderground API information
                            pws = self.config.get('Weather', 'pws')
                            Cn = self.config.getfloat('Weather', 'cn')
                            Cd = self.config.getfloat('Weather', 'cd')
                            
                            try:
                                daily_et = get_daily_et(pws, Cn=Cn, Cd=Cd)
                                _LOGGER.info('Daily ET loss update: %.2f inches', daily_et)
                                
                                for zone in range(1, len(self.hardwareZones)+1):
                                    if self.config.get('Zone%i' % zone, 'enabled') == 'on':
                                        self.hardwareZones[zone-1].current_et_value += daily_et
                                        self.config.set('Zone%i' % zone, 'current_et_value', "%.2f" % self.hardwareZones[zone-1].current_et_value)
                                        
                                self.updatedET = tNow
                                
                            except RuntimeError:
                                _LOGGER.warning('Cannot connect to WUnderground for ET estimate, skipping')
                                
                    ## Figure out if it is the start time or if we are inside a schedule 
                    ## block.  If so, we need to turn things on.
                    ##
                    ## Note:  This needs to include the 'delay' value so that we can 
                    ##        resume things that have been delayed due to weather.
                    tSchedule = tNow.replace(hour=int(h), minute=int(m), second=int(s))
                    tSchedule += self.tDelay
                    if (tNow >= tSchedule and tNow-tSchedule < timedelta(seconds=60)) or self.blockActive:
                        _LOGGER.debug('Scheduling block appears to be starting or active')
                        
                        ### Load in the WUnderground API information
                        pws = self.config.get('Weather', 'pws')
                        
                        ### Check the temperature to see if it is safe to run
                        try:
                            temp = get_current_temperature(pws)
                        except RuntimeError:
                            _LOGGER.warning('Cannot connect to WUnderground for temperature information, skipping check')
                        else:
                            if temp > 35.0:
                                #### Everything is good to go, reset the delay
                                if self.tDelay > timedelta(0):
                                    _LOGGER.info('Resuming schedule after %i hour delay', self.tDelay.seconds/3600)
                                    
                                self.tDelay = timedelta(0)
                                
                            else:
                                #### Wait for an hour an try again...
                                self.tDelay += timedelta(seconds=3600)
                                if self.tDelay >= timedelta(seconds=86400):
                                    self.tDelay = timedelta(0)
                                    
                                    _LOGGER.info('Temperature of %.1f F is below 35 F, delaying schedule for one hour', temp)
                                    _LOGGER.info('New schedule start time will be %s LT', tSchedule+self.tDelay)
                                    
                                    continue
                            _LOGGER.debug('Cleared all weather constraints')
                            
                        ### Load in the last schedule run times
                        previousRuns = self.history.get_data(scheduled_only=True)
                        
                        ### Loop over the zones and work only on those that are enabled
                        for zone in range(1, len(self.hardwareZones)+1):
                            #### Is the current zone even active?
                            if self.config.get('Zone%i' % zone, 'enabled') == 'on':
                                #### What duration do we use for this zone?
                                ##### Get the allowed ET threshold value and convert it to a duration
                                threshold = self.config.getfloat('Schedule%i' % tNow.month, 'threshold')
                                duration = self.hardwareZones[zone-1].get_durations_from_precipitation(threshold)
                                adjustmentUsed = -2.0
                                    
                                duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
                                
                                #### What is the last run time for this zone?
                                tLast = datetime.fromtimestamp( self.hardwareZones[zone-1].get_last_run() )
                                for entry in previousRuns:
                                    if entry['zone'] == zone:
                                        tLast = datetime.fromtimestamp( entry['dateTimeStart'] )
                                        break
                                        
                                if self.hardwareZones[zone-1].is_active():
                                    #### If the zone is active, check how long it has been on
                                    if tNow-tLast >= duration:
                                        self.hardwareZones[zone-1].off()
                                        self.history.write_data(tNowDB, zone, 'off')
                                        _LOGGER.info('Zone %i - off', zone)
                                        _LOGGER.info('  Run Time: %s', (tNow-tLast))
                                    else:
                                        self.blockActive = True
                                        break
                                        
                                else:
                                    #### Otherwise, it might be time to turn it on
                                    if self.blockActive:
                                        #### Have we already tried?
                                        if zone in self.processedInBlock:
                                            continue
                                            
                                    if self.hardwareZones[zone-1].current_et_value >= threshold:
                                        self.hardwareZones[zone-1].on()
                                        self.hardwareZones[zone-1].current_et_value -= threshold
                                        
                                        self.history.write_data(tNowDB, zone, 'on', wx_adjustment=adjustmentUsed)
                                        self.config.set('Zone%i' % zone, 'current_et_value', "%.2f" % self.hardwareZones[zone-1].current_et_value)
                                        
                                        _LOGGER.info('Zone %i - on', zone)
                                        _LOGGER.info('  Last Ran: %s LT (%s ago)', tLast, tNow-tLast)
                                        _LOGGER.info('  Duration: %s', duration)
                                        _LOGGER.info('  Current ET Losses: %.2f"', self.hardwareZones[zone-1].current_et_value)
                                        self.blockActive = True
                                        self.processedInBlock.append( zone )
                                        break
                                        
                            #### If this is the last zone to process and it is off, we
                            #### are done with this block
                            if zone == len(self.hardwareZones) and not self.hardwareZones[zone-1].is_active():
                                self.blockActive = False
                                self.processedInBlock = []
                                
                    else:
                        pass
                            
                else:
                    for zone in range(1, len(self.hardwareZones)+1):
                        zone.current_et_value = 0.0
                        
            except Exception as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                _LOGGER.error("ScheduleProcessor: %s at line %i", e, traceback.tb_lineno(exc_traceback))
                ## Grab the full traceback and save it to a string via StringIO
                fileObject = StringIO.StringIO()
                traceback.print_tb(exc_traceback, file=fileObject)
                tbString = fileObject.getvalue()
                fileObject.close()
                ## Print the traceback to the logger as a series of DEBUG messages
                for line in tbString.split('\n'):
                    _LOGGER.debug("%s", line)
                    
    def _set_daemon(self):
        return True
