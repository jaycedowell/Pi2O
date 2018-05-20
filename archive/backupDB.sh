#!/bin/bash

tag=`date +"%Y%m%d-%H%M%S"`
sqlite3 pi2o-data.db ".backup 'pi2o-${tag}.db.bak'"
