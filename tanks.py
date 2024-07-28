# -*- coding: utf-8 -*

"""
Module for reading in tank conditions from a RainCache device.
"""

import sys
import pytz
import time
import logging
import threading
import traceback
import subprocess
from urllib.request import urlopen
from datetime import datetime

from expiring_cache import expiring_cache

__version__ = '0.1'
__all__ = ['MIN_VALID_DISTANCE', 'get_current_temperature', 'get_current_distance', 'get_current_volume']


MIN_VALID_DISTANCE = 4.0    # Inches


# Logger instance
_LOGGER = logging.getLogger('__main__')


@expiring_cache(maxage=300)
def _poll_raincache(ip, timeout=30):
    """
    Poll a rain cache device at the specified IP address and return a seven-
    element tuple of:
     * the timestamp corresponding to the measurements
     * the system-on-chip temperature in degrees C
     * the tank air temperature in degrees C
     * the distance to the water surface in inches
     * the uncertainity in the distance to the water surface in inches
     * the total water volume in gallons
     * the uncertainity in the total volume in gallons.
    
    If the values cannot be determined all values returned are zero.
    """
    
    t0, s, t, d, de, v, ve = 0, 0, 0, 0, 0, 0, 0
    try:
        with urlopen("http://%s" % ip, timeout=timeout) as uh:
            page = uh.read()
            page = page.decode()
            
        for line in page.split('\n'):
            if line.startswith('SoC'):
                fields = line.split()
                try:
                    s = float(fields[3])
                except:
                    pass
            elif line.startswith('Air'):
                fields = line.split()
                try:
                    t = float(fields[3])
                except:
                    pass
            elif line.startswith('Distance'):
                fields = line.split()
                try:
                    d = float(fields[4])
                    de = float(fields[6])
                except:
                    pass
            elif line.startswith('Current'):
                fields = line.split()
                try:
                    v = float(fields[3])
                    ve = float(fields[5])
                except:
                    pass
            elif line.find(':') != -1:
                t0 = datetime.strptime(line.strip().rstrip().replace('<br>', ''), '%Y/%m/%d %H:%M:%S')
                t0 = pytz.utc.localize(t0).timestamp()
    except:
        pass
        
    return t0, s, t, d, de, v, ve


def get_current_temperature(ip, timeout=30):
    """
    Get the current air temperature in the tanks.  Return a two-
    element tuple of the timestamp when the measurement was made and the
    temperature in degrees C.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0:
        raise RuntimeError("Failed to get current air temperature")
        
    return t0, t


def get_current_distance(ip, timeout=30):
    """
    Get the current distance from the gauge to the water's surface.  Returns a
    three-element tuple of the timestamp when the measurement was made, the
    distance to the water's surface in inches, and the uncertainity of the
    distance in inches.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DISTANCE:
        raise RuntimeError("Failed to get current water surface distance")
        
    return t0, d, de


def get_current_volume(ip, timeout=30):
    """
    Get the current total water volume in the tanks.  Returns a three-element
    tuple of the timestamp when the measurement was made, the total volume in
    gallons, and the uncertainity of the volume in gallons.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DISTANCE:
        raise RuntimeError("Failed to get current water volume")
        
    return t0, v, ve


class TankLogger(object):
    """
    Class responsible for monitoring the water levels in the tanks.
    """

    def __init__(self, config, logname=None):
        self.interval = 600
        self.config = config
        if logname is None:
            logname = '/home/pi/tanks.log'
        self.lognname = logname
        
        self.thread = None
        self.alive = threading.Event()
        
    def start(self):
        if self.thread is not None:
            self.cancel()
                   
        self.thread = threading.Thread(target=self.run, name='tanks')
        self.thread.setDaemon(1)
        self.alive.set()
        self.thread.start()
        
        _LOGGER.info('Started the TankLogger background thread')
        
    def cancel(self):
        if self.thread is not None:
            self.alive.clear()          # clear alive event for thread
            self.thread.join()
            
        _LOGGER.info('Stopped the TankLogger background thread')
        
    def is_alive(self):
        status = False
        if self.thread is not None:
            status = self.thread.is_alive()
        return status
        
    def run(self):
        self.running = True
        
        while self.alive.is_set():
            t0, s, t, de, de, v, ve = _poll_raincache(self.config.get('RainCache', 'ip'), timeout=30)
            
            if t0 > 0 and d >= MIN_VALID_DISTANCE:
                with open(self.logname, 'a') as fh:
                    fh.write(f"{t0},{s},{t},{d},{de},{v},{ve}\n")
                    
            trimmed = subprocess.check_call['tail', '-n2000', self.logname]
            with open(self.logname, 'wb') as fh:
                fh.write(trimmed)
                
            time.sleep(self.interval)
            
    def last_entry(self):
        """
        Return the last line of the log file as the seven-element tuple that
        _poll_raincache() provides.
        """
        
        last_line = subprocess.check_call(['tail', '-n1', self.logname])
        last_line = last_line.decode().strip().rstrip()
        fields = [float(v) for v in last_line.split()]
        return fields


if __name__ == '__main__':
    t0, s, t, de, de, v, ve = _poll_raincache(sys.argv[1], timeout=30)
    
    if t0 > 0 and d >= MIN_VALID_DISTANCE:
        with open('/home/pi/tanks.log', 'a') as fh:
            fh.write(f"{t0},{s},{t},{d},{de},{v},{ve}\n")
