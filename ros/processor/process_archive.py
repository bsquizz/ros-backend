import requests
import logging
import pydash as _
from http import HTTPStatus
from contextlib import contextmanager
from tempfile import NamedTemporaryFile
from insights import extract, rule, run, make_metadata
from insights.parsers.pmlog_summary import PmLogSummary
from insights.parsers.lscpu import LsCPU
from insights.parsers.aws_instance_id import AWSInstanceIdDoc
from insights.parsers.azure_instance_type import AzureInstanceType
from insights.core import dr
from ros.lib.config import INSIGHTS_EXTRACT_LOGLEVEL
from ros.processor.metrics import (archive_downloaded_success,
                                   archive_failed_to_download,
                                   processor_requests_failures)


LOG = logging.getLogger(__name__)
dr.log.setLevel(INSIGHTS_EXTRACT_LOGLEVEL)
prefix = "ARCHIVE PROCESSOR"


@rule(LsCPU, [AWSInstanceIdDoc, AzureInstanceType], optional=[PmLogSummary])
def performance_profile(lscpu, aws_instance_id, azure_instance_type, pmlog_summary):
    profile = {}
    if pmlog_summary is not None:
        performance_metrics = [
            'hinv.ncpu',
            'mem.physmem',
            'mem.util.available',
            'disk.dev.total',
            'kernel.all.cpu.idle',
            'kernel.all.pressure.cpu.some.avg',
            'kernel.all.pressure.io.full.avg',
            'kernel.all.pressure.io.some.avg',
            'kernel.all.pressure.memory.full.avg',
            'kernel.all.pressure.memory.some.avg',
        ]
        for i in performance_metrics:
            if i in ['hinv.ncpu', 'mem.physmem', 'mem.util.available', 'kernel.all.cpu.idle']:
                profile[i] = _.get(pmlog_summary, f'{i}.val')
            else:
                profile[i] = _.get(pmlog_summary, i)

    profile["total_cpus"] = int(lscpu.info.get('CPUs'))
    if aws_instance_id:
        profile["instance_type"] = aws_instance_id.get('instanceType')
        profile["region"] = aws_instance_id.get('region')
    elif azure_instance_type:
        profile["instance_type"] = azure_instance_type.raw
    else:
        profile["instance_type"] = None
        profile["region"] = None

    metadata_response = make_metadata()
    metadata_response.update(profile)
    return metadata_response


def get_performance_profile(report_url, org_id, custom_prefix=prefix):
    with _download_and_extract_report(report_url, org_id, custom_prefix=custom_prefix) as archive:
        try:
            LOG.debug(
                "%s - Extracting performance profile from the report present at %s.\n",
                custom_prefix, report_url)
            broker = run(performance_profile, root=archive.tmp_dir)
            result = broker[performance_profile]
            del result["type"]
            LOG.debug(
                "%s - Extracted performance profile from the report successfully present at %s.\n",
                custom_prefix, report_url)
            return result
        except Exception as e:
            processor_requests_failures.labels(
                reporter='INSIGHTS ENGINE', org_id=org_id
            ).inc()
            LOG.error("%s - Failed to extract performance_profile from the report present at %s. ERROR - %s\n",
                      custom_prefix, report_url, e)


@contextmanager
def _download_and_extract_report(report_url, org_id, custom_prefix=prefix):
    download_response = requests.get(report_url)
    LOG.info("%s - Downloading the report from %s.\n", custom_prefix, report_url)

    if download_response.status_code != HTTPStatus.OK:
        archive_failed_to_download.labels(org_id=org_id).inc()
        LOG.error("%s - Unable to download the report from %s. ERROR - %s\n",
                  custom_prefix, report_url, download_response.reason)
    else:
        archive_downloaded_success.labels(org_id=org_id).inc()
        with NamedTemporaryFile() as tf:
            tf.write(download_response.content)
            LOG.debug("%s - Downloaded the report successfully from %s.\n", custom_prefix, report_url)
            tf.flush()
            with extract(tf.name) as ex:
                yield ex
