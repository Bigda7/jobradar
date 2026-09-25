"""Source adapter implementations."""

from jobradar.sources.base import BaseSource
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.dou_jobs import DouJobsSource
from jobradar.sources.mock import MockSource
from jobradar.sources.robota_ua import RobotaUaSource
from jobradar.sources.workua import WorkUaSource

__all__ = [
    "BaseSource",
    "DjinniSource",
    "DouJobsSource",
    "MockSource",
    "RobotaUaSource",
    "WorkUaSource",
]
