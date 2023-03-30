Pi2O
====

Raspberry Pi-based sprinkler controller that supports automatic zone run time adjustments
via the WUnderground API and a software rain sensor.

Requirements
------------
 * Python >= 3.6
 * cherrypy >= 3.0
 * jinja2
 * sqlite3
 * A Raspberry Pi with the board described in the `schematics` directory
 * the name of a WUnderground PWS if you want to use a software rain sensor or
   automatic runtime adjustment

Usage
-----
  1) Connect the Raspberry Pi to the control board and supply 24 VAC power
  
  2) Create the sqlite3 database using the 'archive/initDB.sh' script
  
  3) Install the pi2o logrotate configuration file in /etc/logrotate.d/
  
  4) Run the script via './Pi2O.py'
  
Weather Adjustments
-------------------
The automatic weather adjustments are based on those used by sprinklers_pi with an added
correction for the average wind speed.
