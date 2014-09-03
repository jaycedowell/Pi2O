# -*- coding: utf-8 -*

"""
Module for processing the sprinkler schedules.
"""

import time
import threading
from datetime import datetime, timedelta

from weather import getWeatherAdjustment

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
		
		while self.running:
			time.sleep(self.interval)
			if not self.running:
				return True
				
			try:
				tNow = datetime.now()
				tNow = tNow.replace(second=0, microsecond=0)
				
				# Is the current schedule active?
				if self.config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
					## If so, query the run interval and start time for this block
					interval = int(self.config.get('Schedule%i' % tNow.month, 'interval'))
					h,m = [int(i) for i in self.config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
					
					## Figure out if it is the start time or if we are inside a schedule 
					## block.  If so, we need to turn things on.
					tSchedule = tNow.replace(hour=int(h), minute=int(m))
					if tSchedule == tNow or self.blockActive:
						### Load in the current weather adjustment, if needed
						if self.wxAdjust is None:
							key = self.config.get('Weather', 'key')
							pws = self.config.get('Weather', 'pws')
							pos = self.config.get('Weather', 'postal')
							if key != '' and pws != '' and pos != '':
								self.wxAdjust = getWeatherAdjustment(key, pws=pws, postal=pos)
								if self.bus is not None:
									self.bus.log('Setting weather adjustment factor to %.3f' % self.wxAdjust)
							else:
								self.wxAdjust = 1.0
								
						### Convert the interval into a timedeltas
						interval = timedelta(days=interval)
						
						### Load in the last schedule run times
						previousRuns = self.history.getData(scheduledOnly=True)
						
						### Loop over the zones
						for zone in range(1, len(self.hardwareZones)+1):
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
									
							if self.hardwareZones[zone-1].isActive():
								#### If the zone is active, check how long it has been on
								if tLast-tNow >= duration:
									self.hardwareZones[zone-1].off()
									self.history.writeData(time.time(), zone, 'off')
									if self.bus is not None:
										self.bus.log('Zone %i - off' % zone)
								else:
									self.blockActive = True
									break
									
							else:
								#### Otherwise, is it time to turn it on
								if tSchedule - tLast >= interval:
									self.hardwareZones[zone-1].on()
									self.history.writeData(time.time(), zone, 'on', wxAdjustment=self.wxAdjust)
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
					
			except Exception:
				if self.bus is not None:
					self.bus.log("Error in background task thread function.", level=40, traceback=True)
					
	def _set_daemon(self):
		return True
