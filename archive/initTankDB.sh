#!/bin/bash

rm -f pi2o-tanks.db
sqlite3 pi2o-tanks.db < pi2o-tanks.sql
