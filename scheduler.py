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

__version__ = '0.6'
__all__ = ['ScheduleProcessor', '__version__']


# Logger instance
_LOGGER = logging.getLogger('__main__')


class ScheduleProcessor(object):
    """
    Class responsible to running the various zones according to the schedule.
    """

    def __init__(self, config, hardwareZones, history):
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
        
    def is_alive(self):
        status = False
        if self.thread is not None:
            status = self.thread.is_alive()
        return status
        
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
                tNowDB = datetime.utcnow()
                tNowDB = tNowDB.replace(microsecond=0)
                tNowDB = int(tNowDB.strftime("%s"))
                _LOGGER.debug('Starting scheduler polling at %s LT', tNow)
                
                # Is the current schedule active?
                if self.config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
                    ## If so, query the start time for this block
                    h,m = [int(i) for i in self.config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
                    s = 0
                    _LOGGER.debug('Current month of %s is enabled with a start time of %i:%02i:%02i LT', tNow.strftime("%B"), h, m, s)
                    
                    ## Load in the zones to skip for this month
                    zones_to_skip = self.config.get_aslist('Schedule%i' % tNow.month, 'zones_to_skip')
                    if len(zones_to_skip):
                        if zones_to_skip[0] is not None:
                            _LOGGER.debug('Zones %s will be skipped for this month', ','.join([str(z) for z in zones_to_skip]))
                            
                    ## Load in the schedule limiter option
                    run_only_N = 32
                    if self.config.get('Schedule', 'limiter') == 'on':
                        run_only_N = self.config.getint('Schedule', 'max_zones')
                        if run_only_N <= 0:
                            run_only_N = 32
                            
                    ## Update the ET values within one hour of midnight
                    if tNow - tNow.replace(hour=0, minute=0, second=0) < timedelta(hours=1):
                        if tNow - self.updatedET >= timedelta(days=1):
                            ### Load in the WUnderground API information
                            pws = self.config.get('Weather', 'pws')
                            Kc = self.config.getfloat('Weather', 'kc')
                            Cn = self.config.getfloat('Weather', 'cn')
                            Cd = self.config.getfloat('Weather', 'cd')
                            
                            try:
                                daily_et = get_daily_et(pws, Kc=Kc, Cn=Cn, Cd=Cd)
                                _LOGGER.info('Daily ET loss update: %.2f inches', daily_et)
                                
                                for zone in range(1, len(self.hardwareZones)+1):
                                    if zone in zones_to_skip:
                                        self.hardwareZones[zone-1].current_et_value = 0.0
                                    elif self.config.get('Zone%i' % zone, 'enabled') == 'on':
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
                            if self.config.get('Zone%i' % zone, 'enabled') == 'on' \
                               and zone not in zones_to_skip:
                                #### What duration do we use for this zone?
                                ##### Get the allowed ET threshold value and convert it to a duration
                                threshold = self.config.getfloat('Schedule%i' % tNow.month, 'threshold')
                                duration = self.hardwareZones[zone-1].get_durations_from_precipitation(threshold)
                                adjustmentUsed = -2.0
                                    
                                duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
                                
                                #### What is the last run time for this zone?
                                tLastDB = datetime.utcfromtimestamp( self.hardwareZones[zone-1].get_last_run() )
                                for entry in previousRuns:
                                    if entry['zone'] == zone:
                                        tLastDB = datetime.utcfromtimestamp( entry['dateTimeStart'] )
                                        break
                                        
                                if self.hardwareZones[zone-1].is_active():
                                    #### If the zone is active, check how long it has been on
                                    if tNowDB-tLastDB >= duration:
                                        self.hardwareZones[zone-1].off()
                                        self.history.write_data(tNowDB, zone, 'off')
                                        _LOGGER.info('Zone %i - off', zone)
                                        _LOGGER.info('  Run Time: %s', (tNowDB-tLastDB))
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
                                        if len(self.processedInBlock) >= run_only_N:
                                            action_taken = "skipping (%i zones have already ran today)" % run_only_N
                                        else:
                                            action_taken = 'on'
                                            
                                            self.hardwareZones[zone-1].on()
                                            self.hardwareZones[zone-1].current_et_value -= threshold
                                            
                                            self.history.write_data(tNowDB, zone, 'on', wx_adjustment=adjustmentUsed)
                                            self.config.set('Zone%i' % zone, 'current_et_value', "%.2f" % self.hardwareZones[zone-1].current_et_value)
                                            
                                        _LOGGER.info('Zone %i - %s', zone, action_taken)
                                        _LOGGER.info('  Last Ran: %s UTC (%s ago)', tLastDB, tNowDB-tLastDB)
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
                        if zone in zones_to_skip:
                            continue
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
