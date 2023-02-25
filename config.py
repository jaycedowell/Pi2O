# -*- coding: utf-8 -*-

"""
File for dealing with the configuration of Pi2O.py.
"""

import os
import logging
import threading
from configparser import SafeConfigParser, NoSectionError

from zone import GPIORelay, GPIORainSensor, NullRainSensor, SoftRainSensor, SprinklerZone

__version__ = '0.5'
__all__ = ['CONFIG_FILE', 'LockingConfigParser', 'load_config', 'init_zones',
           'save_config']


# Logger instance
confLogger = logging.getLogger('__main__')


# Maximum number of zones to configure
MAX_ZONES = 6


# Files
## Base path for the various files needed/generated by Pi2O.py
_BASE_PATH = os.path.dirname(os.path.abspath(__file__))

## Configuration
CONFIG_FILE = os.path.join(_BASE_PATH, 'Pi2O.config')


class LockingConfigParser(SafeConfigParser):
    """
    Sub-class of ConfigParser.SafeConfigParser that wraps the get, set, and 
    write methods with a semaphore to ensure that only one get/set/read/write 
    happens at a time.  The sub-class also adds a 'dict' property that makes
    it easier to tie the configuration into webforms.
    """
    
    _lock = threading.Semaphore()
    
    def get(self, *args, **kwds):
        """
        Locked get() method.
        """
        
        #self._lock.acquire()
        value = SafeConfigParser.get(self, *args, **kwds)
        #self._lock.release()
        return value
        
    def getint(self, *args, **kwds):
        """
        Locked getint() method.
        """
        
        value = SafeConfigParser.getint(self, *args, **kwds)
        return int(value)
        
    def getfloat(self, *args, **kwds):
        """
        Locked getfloat() method.
        """
        
        value = SafeConfigParser.getfloat(self, *args, **kwds)
        return float(value)
        
    def set(self, *args, **kwds):
        """
        Locked set() method.
        """
        
        #self._lock.acquire()
        SafeConfigParser.set(self, *args, **kwds)
        #self._lock.release()
        
    def read(self, *args, **kwds):
        """
        Locked read() method.
        """
        
        SafeConfigParser.read(self, *args, **kwds)
        
    def write(self, *args, **kwds):
        """
        Locked write() method.
        """
        
        SafeConfigParser.write(self, *args, **kwds)
        
    @property
    def dict(self):
        """
        Return the configuration as a dictionary with keys structured as
        section-option.
        """
        
        configDict = {}
        for section in self.sections():
            for keyword,value in self.items(section):
                configDict['%s-%s' % (section.lower(), keyword.replace('_', '-'))] = value
        
        # Done
        return configDict
        
    @dict.setter
    def dict(self, configDict):
        """
        Given a dictionary returned by the dict attribute, update the
        configuration as needed.
        """
        
        # Loop over the pairs in the dictionary
        assert(isinstance(configDict, dict))
        for key,value in configDict.items():
            try:
                section, keyword = key.split('-', 1)
                keyword = keyword.replace('-', '_')
                section = section.capitalize()
                if section == 'Rainsensor':
                    section = 'RainSensor'
                self.set(section, keyword, value)
            except Exception as e:
                print(str(e))
                pass


def load_config(filename):
    """
    Read in the configuration file and return a LockingConfigParser instance.
    """
    
    # Initial configuration file
    config = LockingConfigParser()
    
    ## Dummy information about the four zones:
    ##  1) name - zone nickname
    ##  2) pin - RPi GPIO pin
    ##  3) enabled - whether or not the zone is active
    for zone in range(1, MAX_ZONES+1):
        config.add_section(f"Zone{zone}")
        for keyword in ('name', 'pin', 'enabled'):
            config.set(f"Zone{zone}", keyword, '')
            if keyword == 'enabled':
                config.set(f"Zone{zone}", keyword, 'off')
                
    ## Dummy rain sensor information
    ##  1) type - off, software, or hardware
    ##  2) pin - RPi GPIO pin for the hardware rain sensor
    ##  3) precip - precipitation cutoff for the software rain sensor
    config.add_section('RainSensor')
    config.set('RainSensor', 'type', 'off')
    config.set('RainSensor', 'pin', '')
    config.set('RainSensor', 'precip', '')
    
    ## Dummy schedule information - one for each month
    ##  1) start - start time as HH:MM, 24-hour format
    ##  2) duration - duration in minutes
    ##  3) interval - run interval in days
    ##  4) enabled - whether or not the schedule is active
    ##  5) wxadjust - whether or not weather adjustments should be applied
    for month in range(1, 13):
        config.add_section(f"Schedule{month}")
        for keyword in ('start', 'duration', 'interval', 'enabled', 'wxadjust'):
            if keyword == 'duration':
                for zone in range(1, MAX_ZONES+1):
                    config.set(f"Schedule{month}", f"{keyword}{zone}", '')
            else:
                config.set(f"Schedule{month}", keyword, '')
            if keyword in ('enabled', 'wxadjust'):
                config.set(f"Schedule{month}", keyword, 'off')
                
    ## Dummy weather station information
    ##  1) pws - PWS ID to use for weather info
    ##  2) max_adjust - maximum weather adjustment percentage
    ##  3) enabled - whether or not use to WUnderground
    config.add_section('Weather')
    for keyword in ('pws', 'max_adjust', 'enabled'):
        config.set('Weather', keyword, '')
        if keyword == 'enabled':
            config.set('Weather', keyword, 'off')
        if keyword == 'max_adjust':
            config.set('Weather', keyword, '200')
            
    # Try to read in the actual configuration file
    try:
        config.read(filename)
        confLogger.info('Loaded configuration from \'%s\'', os.path.basename(filename))
        
    except:
        pass
        
    # Done
    return config


def init_zones(config):
    """
    Given a LockingConfigParser configuration instance, create a list of 
    SprinklerZone instances to control the various zones.
    """
    
    # Initialize the rain sensor
    if config.get('RainSensor', 'type') == 'off':
        rainSensor = NullRainSensor()
    elif config.get('RainSensor', 'type') == 'software':
        rainSensor = SoftRainSensor( config.getfloat('RainSensor', 'precip'), config )
    else:
        rainSensor = GPIORainSensor( config.getint('RainSensor', 'pin') )
        
    # Create the list of SprinklerZone instances
    zones = []
    zone = 1
    while True:
        try:
            ## Is the zone enabled?
            zoneEnabled = config.get(f"Zone{zone}", 'enabled')
            if zoneEnabled == 'on':
                ### If so, use the real GPIO pin
                zonePin = config.getint(f"Zone{zone}", 'pin')
                ### If not, use a dummy pin
            else:
                zonePin = -1
                
            ## Create the SprinklerZone instance
            zones.append( SprinklerZone(zonePin, rainSensor=rainSensor) )
            
            ## Update the counter
            zone += 1
            
        except NoSectionError:
            break
            
    # Done
    return zones


def save_config(filename, config):
    """
    Given a filename and a LockingConfigParser, write the configuration to 
    disk.
    """
    
    with open(filename, 'w') as fh:
        config.write(fh)
        
    confLogger.info('Saved configuration to \'%s\'', os.path.basename(filename))
