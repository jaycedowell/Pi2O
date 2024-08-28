# -*- coding: utf-8 -*

"""
Module for controlling a particular sprinkler zone.
"""

import time
import logging

__version__ = '0.3'
__all__ = ['GPIORelay', 'SprinklerZone']


# Logger instance
_LOGGER = logging.getLogger('__main__')


class GPIORelay(object):
    """
    Class for controlling a relay that is active when high via GPIO.
    """
    
    def __init__(self, pin):
        """
        Initialize the class with a GPIO pin number and make sure that the 
        specified as been exported via /sys/class/gpio/export and that it 
        is marked for output.
        """
    
        # GPIO pin
        self.pin = int(pin)
        
        # Setup
        if self.pin > 0:
            try:    
                # Export
                with open('/sys/class/gpio/export', 'w') as fh:
                    fh.write(str(self.pin))
                    fh.flush()
                    
                # Direction
                with open(f"/sys/class/gpio/gpio{self.pin}/direction", 'w') as fh:
                    fh.write('out')
                    fh.flush()
                    
                # Off
                self.off()
                
            except IOError:
                pass
                
    def on(self):
        """
        Turn the relay on.
        """
        
        if self.pin > 0:
            with open(f"/sys/class/gpio/gpio{self.pin}/value", 'w') as fh:
                fh.write('1')
                fh.flush()
                
    def off(self):
        """
        Turn the relay off.
        """
    
        if self.pin > 0:
            with open(f"/sys/class/gpio/gpio{self.pin}/value", 'w') as fh:
                fh.write('0')
                fh.flush()


class SprinklerZone(object):
    """
    Class for controlling a particular sprinkler zone.
    """
    
    def __init__(self, relay, rate=0.0, current_et_value=0.0):
        """
        Create a new SprinklerZone using:
         * relay - a relay GPIO pin number or GPIORelay instance,
         * rate - zone precipation rate in [inches or mm] per hour, and
         * current_et_value - current evapotranspiration value in the same
                              same volume units as "rate".
        """
        
        if type(relay) == GPIORelay:
            self.relay = relay
        else:
            self.relay = GPIORelay(relay)
        self.relay.off()
        self.rate = rate
        self.current_et_value = current_et_value
        
        self.state = 0
        self.lastStart = 0
        self.lastStop = 0
        
    def on(self):
        """
        Turn the sprinkler zone on.
        """
        
        if self.state == 0:
            self.relay.on()
            _LOGGER.info('Turned on GPIO pin %i', self.relay.pin)
            
            self.state = 1
            self.lastStart = time.time()
            
    def off(self):
        """
        Turn the sprinkler zone off.
        """
        
        if self.state == 1:
            self.relay.off()
            _LOGGER.info('Turned off GPIO pin %i', self.relay.pin)
            
            self.state = 0
            self.lastStop = time.time()
            
    @property
    def is_active(self):
        """
        Whether or not the zone is currently on.
        """
        
        return True if self.state else False
        
    def get_last_run(self):
        """
        Return the timestamp of when the zone was last turned on.
        """
        
        return self.lastStart
        
    def get_duration_from_precipitation(self, precip):
        """
        Given a desired precipation amount in inches/mm, return how many minutes
        the zone will need to be on in order to deliever that.
        
        .. note:: The units for the precipation are tied to whatever was used
                  when initializing the 'rate' keyword.
        """
        
        return precip / self.rate * 60.0
        
    def get_precipitation_from_duration(self, duration_min):
        """
        Given a zone run time in minutes, return how much water was delievered
        in inches/mm.
        
        .. note:: The units for the precipation are tied to whatever was used
                  when initializing the 'rate' keyword.
        """
        
        return self.rate * duration_min / 60.0
