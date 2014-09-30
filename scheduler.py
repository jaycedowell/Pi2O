# -*- coding: utf-8 -*

"""
Module for processing the sprinkler schedules.
"""

import time
import threading
from datetime import datetime, timedelta

from weather import getCurrentTemperature, getWeatherAdjustment

__version__ = "0.1"
__all__ = ["ScheduleProcessor", "__version__", "__all__"]


class ScheduleProcessor(threading.Thread):
	"""
	Class responsible to running the various zones according to the schedule.
	"""

	def __init__(self, config, hardwareZones, history, bus=None):
		threading.Thread.__init__(self)
		self.interval = 60
		self.config = config
		self.hardwareZones = hardwareZones
		self.history = history
		self.bus = bus
		
		self.running = False

	def cancel(self):
		self.running = False

	def run(self):
		self.running = True
		self.wxAdjust = None
		self.blockActive = False
		
		self.tDelay = timedelta(0)
		
		while self.running:
			time.sleep(self.interval)
			if not self.running:
				return True
				
			try:
				tNow = datetime.now()
				tNow = tNow.replace(second=0, microsecond=0)
				tNowDB = int(tNow.strftime("%s"))
				
				# Is the current schedule active?
				if self.config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
					## If so, query the run interval and start time for this block
					interval = int(self.config.get('Schedule%i' % tNow.month, 'interval'))
					h,m = [int(i) for i in self.config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
					
					## Figure out if it is the start time or if we are inside a schedule 
					## block.  If so, we need to turn things on.
					##
					## Note:  This needs to include the 'delay' value so that we can 
					##        resume things that have been delayed due to weather.
					tSchedule = tNow.replace(hour=int(h), minute=int(m))
					tSchedule += self.tDelay
					if self.bus is not None:
						self.bus.log('Current Time:  %s' % tNow)
						self.bus.log('Schedule Time: %s' % tSchedule)
						self.bus.log('Schedule Block Active: %s' % self.blockActive)
						self.bus.log('Start Block?   %s' % (tSchedule == tNow or self.blockActive,))
					if tSchedule == tNow or self.blockActive:
						### Load in the WUnderground API information
						key = self.config.get('Weather', 'key')
						pws = self.config.get('Weather', 'pws')
						pos = self.config.get('Weather', 'postal')
						enb = self.config.get('Weather', 'enabled')
						
						### Check the temperature to see if it is safe to run
						if key != '' and (pws != '' or pos != '') and enb == 'on':
							temp = getCurrentTemperature(key, pws=pws, postal=pos)
							if temp > 35.0:
								#### Everything is good to go, reset the delay
								if self.bus is not None:
									if self.tDelay > timedelta(0):
										self.bus.log('Resuming schedule after %i hour delay' % self.tDelay.seconds/3600)
										
								self.tDelay = timedelta(0)
								
							else:
								#### Wait for an hour an try again...
								self.tDelay += timedelta(3600)
								if self.tDelay >= timedelta(86400):
									self.tDelay = timedelta(0)
									
								if self.bus is not None:
									self.bus.log('Temperature of %.1f is below 35, delaying for one hour' % temp)
									
								continue
								
						### Load in the current weather adjustment, if needed
						if self.wxAdjust is None:
							if key != '' and (pws != '' or pos != '') and enb == 'on':
								self.wxAdjust = getWeatherAdjustment(key, pws=pws, postal=pos)
								if self.bus is not None:
									self.bus.log('Setting weather adjustment factor to %.3f' % self.wxAdjust)
							else:
								self.wxAdjust = 1.0
								
						### Convert the interval into a timedeltas
						interval = timedelta(days=interval)
						
						### Load in the last schedule run times
						previousRuns = self.history.getData(scheduledOnly=True)
						
						### Loop over the zones and work only on those that are enabled
						for zone in range(1, len(self.hardwareZones)+1):
							if self.config.get('Zone%i' % zone, 'enabled') == 'on':
								#### What duration do we use for this zone?
								duration = int(self.config.get('Schedule%i' % tNow.month, 'duration%i' % zone))
								duration = duration*self.wxAdjust
								duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
							
								#### What is the last run time for this zone?
								tLast = datetime.fromtimestamp( self.hardwareZones[zone-1].getLastRun() )
								for entry in previousRuns:
									if entry['zone'] == zone:
										tLast = datetime.fromtimestamp( entry['dateTimeStart'] )
										break
										
								if self.bus is not None:
									self.bus.log('Zone #%i of %i' % (zone, len(self.hardwareZones)))
									self.bus.log('  Last Run Time: %s' % tLast)
									self.bus.log('  Zone Interval: %s' % interval)
									self.bus.log('  Zone Duration: %s' % duration)
									self.bus.log('  Current Interval: %s' % (tNow-tLast))
									self.bus.log('  Current Run Time: %s' % (tNow-tLast))
									
								if self.hardwareZones[zone-1].isActive():
									#### If the zone is active, check how long it has been on
									if tNow-tLast >= duration:
										self.hardwareZones[zone-1].off()
										self.history.writeData(tNowDB, zone, 'off')
										if self.bus is not None:
											self.bus.log('Zone %i - off' % zone)
									else:
										self.blockActive = True
										break
									
								else:
									#### Otherwise, is it time to turn it on
									if tNow - tLast >= interval:
										self.hardwareZones[zone-1].on()
										self.history.writeData(tNowDB, zone, 'on', wxAdjustment=self.wxAdjust)
										if self.bus is not None:
											self.bus.log('Zone %i - on' % zone)
										self.blockActive = True
										break
										
							#### If this is the last zone to process and it is off, we
							#### are done with this block
							if zone == len(self.hardwareZones) and not self.hardwareZones[zone-1].isActive():
								self.blockActive = False
								
					else:
							self.wxAdjust = None
								
				else:
					self.wxAdjust = None
					
			except Exception:
				if self.bus is not None:
					self.bus.log("Error in background task thread function.", level=40, traceback=True)
					
	def _set_daemon(self):
		return True
