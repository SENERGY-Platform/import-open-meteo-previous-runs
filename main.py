# Copyright 2026 InfAI (CC SES)
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.


import sched

import time
from import_lib.import_lib import ImportLib, get_logger

from lib.OpenMeteoPreviousRunsImport import OpenMeteoPreviousRunsImport

if __name__ == '__main__':
    lib = ImportLib("github.com/SENERGY-Platform/import-open-meteo-previous-runs")
    logger = get_logger(__name__)
    scheduler = sched.scheduler(timefunc=time.time)
    previous_runs_import = OpenMeteoPreviousRunsImport(lib, scheduler)

    while True:
        scheduler.run()
        time.sleep(10)
