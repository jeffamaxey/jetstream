import logging
import os
import re
import shlex
import subprocess
import time
from collections import OrderedDict

import ulid

log = logging.getLogger(__name__)

submission_pattern = re.compile("Submitted batch job (\d*)")
states = {
    'BOOT_FAIL': 'Job terminated due to launch failure, typically due to a '
                 'hardware failure (e.g. unable to boot the node or block '
                 'and the job can not be requeued).',
    'CANCELLED': 'Job was explicitly cancelled by the user or system '
                 'administrator. The job may or may not have been '
                 'initiated.',
    'COMPLETED': 'Job has terminated all processes on all nodes with an '
                 'exit code of zero.',
    'CONFIGURING': 'Job has been allocated resources, but are waiting for '
                   'them to become ready for use (e.g. booting).',
    'COMPLETING': 'Job is in the process of completing. Some processes on '
                  'some nodes may still be active.',
    'FAILED': 'Job terminated with non-zero exit code or other failure '
              'condition.',
    'NODE_FAIL': 'Job terminated due to failure of one or more allocated '
                 'nodes.',
    'PENDING': 'Job is awaiting resource allocation.',
    'PREEMPTED': 'Job terminated due to preemption.',
    'REVOKED': 'Sibling was removed from cluster due to other cluster '
               'starting the job.',
    'RUNNING': 'Job currently has an allocation.',
    'SPECIAL_EXIT': 'The job was requeued in a special state. This state '
                    'can be set by users, typically in EpilogSlurmctld, if '
                    'the job has terminated with a particular exit value.',
    'STOPPED': 'Job has an allocation, but execution has been stopped with '
               'SIGSTOP signal. CPUS have been retained by this job.',
    'SUSPENDED': 'Job has an allocation, but execution has been suspended '
                 'and CPUs have been released for other jobs.',
    'TIMEOUT': 'Job terminated upon reaching its time limit.'
}
active_states = {'CONFIGURING', 'COMPLETING', 'RUNNING', 'SPECIAL_EXIT',
                 'PENDING'}
inactive_states = {'BOOT_FAIL', 'CANCELLED', 'COMPLETED', 'FAILED',
                   'NODE_FAIL', 'PREEMPTED', 'REVOKED',
                   'STOPPED', 'SUSPENDED', 'TIMEOUT'}
failed_states = {'BOOT_FAIL', 'CANCELLED', 'FAILED', 'NODE_FAIL'}
completed_states = {'COMPLETED'}

_update_frequency = os.environ.get('SLURM_UPDATE_FREQUENCY', 1)
_max_update_wait = os.environ.get('SLURM_MAX_UPDATE_WAIT', 3600)


class SacctOutput(Exception):
    pass


class SlurmJob(object):
    _instances = set()

    def __init__(self, jid, cluster=None, sacct=None):
        """Tracks a Slurm job and provides some utility methods like wait() and
        update().

        If sacct is None, the job record will be requested with 'sacct'. An
        existing sacct record can be used instead by giving a mapping as sacct.
        This was added so that batches of job records can be requested from
        Slurm manually.

        For example:

            jids = [j.jid for j in slurm_jobs]
            records = query_sacct(*jids)
            slurm_jobs = [SlurmJob(sacct=r) for r in records]

        This concept is implemented in the get_jobs() function.
        """

        self.jid = int(jid)
        self.cluster = cluster

        if sacct is not None:
            self.sacct = sacct
        else:
            self.update()

        SlurmJob._instances.add(self)

    def __repr__(self):
        return "SlurmJob(%s)" % self.jid

    def serialize(self):
        return self.__dict__

    @property
    def status(self):
        try:
            return self.sacct['State']
        except KeyError:
            return 'unknown'

    @property
    def is_active(self):
        if self.status in active_states:
            return True
        else:
            return False

    @property
    def is_failed(self):
        if self.status in failed_states:
            return True
        else:
            return False

    @property
    def is_complete(self):
        if self.status in completed_states:
            return True
        else:
            return False

    def wait(self, timeout=None):
        start = time.time()
        while 1:
            elapsed = time.time() - start
            self.update()
            if not self.is_active:
                break
            else:
                if timeout and elapsed > timeout:
                    raise TimeoutError

        return self.status

    def _get_sacct(self):
        sacct_data = query_sacct(self.jid)
        matches = [r for r in sacct_data if int(r['JobID']) == self.jid]

        if len(matches) > 1:
            msg = "Sacct returned more than one record for {}".format(self.jid)
            raise SacctOutput(msg)
        elif len(matches) == 0:
            msg = "Sacct returned no records for {}".format(self.jid)
            raise SacctOutput(msg)
        else:
            return matches[0]

    def update(self):
        """Request a status update from Slurm. """
        start = time.time()
        while 1:
            elapsed = time.time() - start
            try:
                self.sacct = self._get_sacct()
                break
            except SacctOutput:
                if elapsed > _max_update_wait:
                    raise


def get_jobs(*args, **kwargs):
    """ Run batch query for slurm jobs, returns a list of SlurmJobs """
    jobs = []
    records = query_sacct(*args, **kwargs)
    for rec in records:
        s = SlurmJob(rec['JobID'], sacct=rec)
        jobs.append(s)

    return jobs


def wait_for(*jobs, timeout=None):
    # TODO batch query job status
    if not jobs:
        raise ValueError('No jobs given!')

    else:
        jids = list()
        for job in jobs:
            if isinstance(job, SlurmJob):
                jids.append(job.jid)
            else:
                jids.append(int(job))

    start = time.time()
    tracker = {j: True for j in jobs}
    while 1:
        elapsed = time.time() - start
        incomplete = {k: v for k, v in tracker.items() if v}

        if not incomplete:
            break
        else:
            for job in incomplete.keys():
                job.update()
                tracker[job] = job.is_active
                if timeout and elapsed > timeout:
                    raise TimeoutError


def query_sacct(*job_ids, all=False):
    """ Run sacct query for given job_ids and returns a list of records """
    log.debug('query_sacct: {}'.format(str(job_ids)))

    cmd_prefix = ['sacct', '-XP', '--format', 'all']

    if job_ids:
        job_ids = ' '.join(['-j %s' % jid for jid in job_ids if jid])
        cmd_args = cmd_prefix + shlex.split(job_ids)
    else:
        if not all:
            raise ValueError('must give job ids or specify all=True')
        cmd_args = cmd_prefix

    log.debug('Launching: %s' % ' '.join(cmd_args))

    res = subprocess.check_output(cmd_args).decode()
    time.sleep(_update_frequency)  # Premature optimization

    # Convert the sacct report to an object
    records = []
    lines = res.splitlines()
    header = lines.pop(0).strip().split('|')
    for line in lines:
        row = OrderedDict(zip(header, line.strip().split('|')))
        records.append(row)

    return records


def srun(*args):
    """ Srun a command, additional args are added to srun prefix """
    cmd_args = ('srun',) + args
    log.debug('launching: {}'.format(cmd_args))
    return subprocess.check_output(cmd_args)


def sbatch(*args, stdin_data=None):
    cmd_args = ('sbatch', '--parsable') + args

    log.critical('launching: {}'.format(cmd_args))
    p = subprocess.Popen(
        cmd_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE
    )

    stdout, stderr = p.communicate(input=stdin_data)

    if p.returncode != 0:
        raise ChildProcessError(str(vars(p)))

    jid, _, cluster = stdout.decode().strip().partition(';')
    log.critical("Submitted batch job {}".format(jid))
    return SlurmJob(jid, cluster=cluster)


def easy(cmd, *args, module_load=None):
    """Launch shell scripts on slurm with controlled environments via module """
    subprocess.check_call(['sbatch', '--version'])

    if not os.path.exists('logs') or not os.path.isdir('logs'):
        log.critical('Creating log dir')
        os.makedirs('logs', exist_ok=True)

    job_name = ulid.new().str
    log.critical('Slurm Easy Unique job name: {}'.format(job_name))
    sbatch_args = args + ('-J', job_name, '-o', 'logs/%x_slurm-%A.out')

    if module_load:
        module_cmd = "module load {}".format(module_load)
    else:
        module_cmd = "# No additional modules loaded'"

    template= (
        "#!/bin/bash\n"            
        "# Generated with jetstream.scriptkit.slurm.easy()\n"
        "# sbatch {sbatch_args}\n"
        "# ---\n"
        "{module_cmd}\n"
        "{script}\n"
    )

    script = template.format(
        sbatch_args=' '.join(sbatch_args),
        module_cmd=module_cmd,
        script=cmd
    )

    log.critical('Final script to be submitted:\n{}'.format(script))

    with open('logs/{}.sh'.format(job_name), 'w') as fp:
        fp.write(script)

    job = sbatch(*sbatch_args, stdin_data=script.encode())
    return job