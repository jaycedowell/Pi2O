Pi2O
====

Raspberry Pi-based sprinkler controller that supports automatic zone run time adjustments
via the WUnderground API and both software and hardware rain sensors.

Requirements
------------
 * Python >=2.7 and <3.0
 * cherrypy >= 3.0
 * jinja2
 * sqlite3
 * a relay board that activates on high
 * the name of a WUnderground PWS with a solar radiation sensor to use

Usage
-----
  1) Wire up the relay board to the RPi's GPIO pins such that the relay is activated when
  then pin goes high.
  
  2) Optionally wire up a rain sensor via another GPIO pin such that the pin goes high 
  when the sensor is active.
  
  3) Create the sqlite3 database using the 'archive/initDB.sh' script
  
  4) Install the pi2o logrotate configuration file in /etc/logrotate.d/
  
  5) Run the script via './Pi2O.py'
  
Weather Adjustments
-------------------
The amount to water and the frequency of watering is determined by using the integrated
evapotranspiration losses as computed by the Penman-Monteith equation.
