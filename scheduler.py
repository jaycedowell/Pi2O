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

from weather import getCurrentTemperature, getWeatherAdjustment

__version__ = "0.3"
__all__ = ["ScheduleProcessor", "__version__", "__all__"]


# Logger instance
schLogger = logging.getLogger('__main__')


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
		
		schLogger.info('Started the ScheduleProcessor background thread')
		
	def cancel(self):
		if self.thread is not None:
			self.alive.clear()          # clear alive event for thread
			self.thread.join()
			
		schLogger.info('Stopped the ScheduleProcessor background thread')
		
	def run(self):
		self.running = True
		self.wxAdjust = None
		self.blockActive = False
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
				schLogger.debug('Starting scheduler polling at %s LT', tNow)
				
				# Is the current schedule active?
				if self.config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
					## If so, query the run interval and start time for this block
					interval = int(self.config.get('Schedule%i' % tNow.month, 'interval'))
					h,m = [int(i) for i in self.config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
					s = 0
					schLogger.debug('Current month of %s is enabled with a start time of %i:%02i:%02i LT', tNow.strftime("%B"), h, m, s)
					
					## Figure out if it is the start time or if we are inside a schedule 
					## block.  If so, we need to turn things on.
					##
					## Note:  This needs to include the 'delay' value so that we can 
					##        resume things that have been delayed due to weather.
					tSchedule = tNow.replace(hour=int(h), minute=int(m), second=int(s))
					tSchedule += self.tDelay
					if (tNow >= tSchedule and tNow-tSchedule < timedelta(seconds=60)) or self.blockActive:
						schLogger.debug('Scheduling block appears to be starting or active')
						
						### Load in the WUnderground API information
						key = self.config.get('Weather', 'key')
						pws = self.config.get('Weather', 'pws')
						pos = self.config.get('Weather', 'postal')
						enb = self.config.get('Weather', 'enabled')
						
						### Check the temperature to see if it is safe to run
						if key != '' and (pws != '' or pos != '') and enb == 'on':
							try:
								temp = getCurrentTemperature(key, pws=pws, postal=pos)
							except RuntimeError:
								schLogger.warning('Cannot connect to WUnderground for temperature information, skipping check')
							else:
								if temp > 35.0:
									#### Everything is good to go, reset the delay
									if self.tDelay > timedelta(0):
										schLogger.info('Resuming schedule after %i hour delay', self.tDelay.seconds/3600)
										
									self.tDelay = timedelta(0)
									
								else:
									#### Wait for an hour an try again...
									self.tDelay += timedelta(seconds=3600)
									if self.tDelay >= timedelta(seconds=86400):
										self.tDelay = timedelta(0)
										
										schLogger.info('Temperature of %.1f F is below 35 F, delaying schedule for one hour', temp)
										schLogger.info('New schedule start time will be %s LT', tSchedule+self.tDelay)
										
										continue
								schLogger.debug('Cleared all weather constraints')
								
						### Load in the current weather adjustment, if needed
						if self.wxAdjust is None:
							if key != '' and (pws != '' or pos != '') and enb == 'on':
								try:
									self.wxAdjust = getWeatherAdjustment(key, pws=pws, postal=pos)
								except RuntimeError:
									schLogger.warning('Cannot connect to WUnderground for weather adjustment, setting to 100%')
									self.wxAdjust = 1.0
								except Exception as e:
									schLogger.warning('Error computing weather adjustment, setting to 100%')
									self.wxAdjust = 1.0
							else:
								self.wxAdjust = 1.0
							schLogger.info('Set weather adjustment to %.1f%%', self.wxAdjust*100.0)
						
						### Convert the interval into a timedeltas
						interval = timedelta(days=interval)
						schLogger.debug('Run interval for this schedule set to %s', interval)
						
						### Load in the last schedule run times
						previousRuns = self.history.getData(scheduledOnly=True)
						
						### Loop over the zones and work only on those that are enabled
						for zone in range(1, len(self.hardwareZones)+1):
							#### Is the current zone even active?
							if self.config.get('Zone%i' % zone, 'enabled') == 'on':
								#### What duration do we use for this zone?
								duration = int(self.config.get('Schedule%i' % tNow.month, 'duration%i' % zone))
								if self.config.get('Schedule%i' % tNow.month, 'wxadjust') == 'on':
									duration = duration*self.wxAdjust
									adjustmentUsed = self.wxAdjust*1.0
								else:
									duration = duration*1.0
									adjustmentUsed = -2.0
									schLogger.info('Weather adjustment is not enabled for this schedule, ignoring previous value')
									
								duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
								
								#### What is the last run time for this zone?
								tLast = datetime.fromtimestamp( self.hardwareZones[zone-1].getLastRun() )
								for entry in previousRuns:
									if entry['zone'] == zone:
										tLast = datetime.fromtimestamp( entry['dateTimeStart'] )
										break
										
								if self.hardwareZones[zone-1].isActive():
									#### If the zone is active, check how long it has been on
									if tNow-tLast >= duration:
										self.hardwareZones[zone-1].off()
										self.history.writeData(tNowDB, zone, 'off')
										schLogger.info('Zone %i - off', zone)
										schLogger.info('  Run Time: %s', (tNow-tLast))
									else:
										self.blockActive = True
										break
										
								else:
									#### Otherwise, it might be time to turn it on
									if self.blockActive:
										#### Have we already tried?
										if zone in self.processedInBlock:
											continue
											
									if tNow - tLast >= interval - timedelta(hours=3):
										self.hardwareZones[zone-1].on()
										self.history.writeData(tNowDB, zone, 'on', wxAdjustment=adjustmentUsed)
										schLogger.info('Zone %i - on', zone)
										schLogger.info('  Last Ran: %s LT (%s ago)', tLast, tNow-tLast)
										schLogger.info('  Duration: %s', duration)
										self.blockActive = True
										self.processedInBlock.append( zone )
										break
										
							#### If this is the last zone to process and it is off, we
							#### are done with this block
							if zone == len(self.hardwareZones) and not self.hardwareZones[zone-1].isActive():
								self.blockActive = False
								self.processedInBlock = []
								self.wxAdjust = None
								
					else:
							#self.wxAdjust = None
							pass
							
				else:
					self.wxAdjust = None
					
			except Exception, e:
				exc_type, exc_value, exc_traceback = sys.exc_info()
				schLogger.error("ScheduleProcessor: %s at line %i", e, traceback.tb_lineno(exc_traceback))
				## Grab the full traceback and save it to a string via StringIO
				fileObject = StringIO.StringIO()
				traceback.print_tb(exc_traceback, file=fileObject)
				tbString = fileObject.getvalue()
				fileObject.close()
				## Print the traceback to the logger as a series of DEBUG messages
				for line in tbString.split('\n'):
					schLogger.debug("%s", line)
					
	def _set_daemon(self):
		return True
