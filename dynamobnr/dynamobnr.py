#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# dynamodb-bnr - DynamoDB backup'n'restore python script
#                with tarfile management
#
# Raphaël Beamonte <raphael.beamonte@bhvr.com>
#

import argparse
import logging
import multiprocessing
import os
import sys

from . import commands
from .namespace import Namespace

const_parameters = Namespace({
    'json_indent': 2,
    'schema_file': 'schema.json',
    'data_dir': 'data',
    'incomplete_suffix': '~incomplete',
    'throughputexceeded_sleeptime':
        int(os.getenv('DYNAMODB_BNR_THROUGHPUTEXCEEDED_SLEEPTIME', 10)),
    'throughputexceeded_maxretry':
        int(os.getenv('DYNAMODB_BNR_THROUGHPUTEXCEEDED_MAXRETRY', 5)),
    'resourceinuse_sleeptime':
        int(os.getenv('DYNAMODB_BNR_RESOURCEINUSE_SLEEPTIME', 10)),
    'resourceinuse_maxretry':
        int(os.getenv('DYNAMODB_BNR_RESOURCEINUSE_MAXRETRY', 5)),
    'limitexceeded_sleeptime':
        int(os.getenv('DYNAMODB_BNR_LIMITEXCEEDED_SLEEPTIME', 15)),
    'limitexceeded_maxretry':
        int(os.getenv('DYNAMODB_BNR_LIMITEXCEEDED_MAXRETRY', 10)),
    'tableoperation_sleeptime':
        int(os.getenv('DYNAMODB_BNR_TABLEOPERATION_SLEEPTIME', 5)),
    'dynamodb_max_batch_write':
        int(os.getenv('DYNAMODB_BNR_MAX_BATCH_WRITE', 25)),
    'opensslerror_maxretry':
        int(os.getenv('DYNAMODB_BNR_OPENSSLERROR_MAXRETRY', 5)),
    's3_max_delete_objects':
        int(os.getenv('DYNAMODB_BNR_MAX_DELETE_OBJECTS', 1000)),
    'connresetbypeer_sleeptime':
        int(os.getenv('DYNAMODB_BNR_ECONNRESET_MAXRETRY', 15)),
    'connresetbypeer_maxretry':
        int(os.getenv('DYNAMODB_BNR_ECONNRESET_MAXRETRY', 5)),
    'wrongschema_maxretry':
        int(os.getenv('DYNAMODB_BNR_WRONGSCHEMA_MAXRETRY', 5)),
})

parser = None
logger = None
parameters = None
aws = None


def parser_error(errmsg):
    parser.print_usage()
    print('{}: error: {}'.format(
        os.path.basename(sys.argv[0]), errmsg))
    sys.exit(1)


def cli():
    global parameters, logger, aws
    parameters = parse_args()
    parameters.tar_path = None

    logger = logging.getLogger('.'.join(
        os.path.basename(__file__).split('.')[:-1]))

    # Set up amazon configuration
    if parameters.access_key is not None:
        if parameters.ddb_access_key is None:
            parameters.ddb_access_key = parameters.access_key
        if parameters.s3_access_key is None:
            parameters.s3_access_key = parameters.access_key
    if parameters.secret_key is not None:
        if parameters.ddb_secret_key is None:
            parameters.ddb_secret_key = parameters.secret_key
        if parameters.s3_secret_key is None:
            parameters.s3_secret_key = parameters.secret_key
    if parameters.region is not None:
        if parameters.ddb_region is None:
            parameters.ddb_region = parameters.region
        if parameters.s3_region is None:
            parameters.s3_region = parameters.region
    if parameters.profile is not None:
        if parameters.ddb_profile is None:
            parameters.ddb_profile = parameters.profile
        if parameters.s3_profile is None:
            parameters.s3_profile = parameters.profile

    # Check that dynamodb configuration is available if needed
    if parameters.command != 'check' and \
        (parameters.ddb_profile is None and
         (parameters.ddb_access_key is None or
          parameters.ddb_secret_key is None or
          parameters.ddb_region is None)):
        parser_error(('DynamoDB configuration is incomplete '
                      '(access key? {}, secret key? {}, region? {}'
                      ') or profile? {})').format(
            parameters.ddb_access_key is not None,
            parameters.ddb_secret_key is not None,
            parameters.ddb_region is not None,
            parameters.ddb_profile is not None))

    # Check that s3 configuration is available if needed
    if parameters.s3 and \
        (parameters.s3_profile is None and
         (parameters.s3_access_key is None or
          parameters.s3_region is None or
          parameters.s3_secret_key is None or
          parameters.s3_bucket is None)):
        parser_error(('S3 configuration is incomplete '
                      '(access key? {}, secret key? {}, region? {}, '
                      'bucket? {}) or profile {}').format(
            parameters.s3_access_key is not None,
            parameters.s3_secret_key is not None,
            parameters.s3_region is not None,
            parameters.s3_bucket is not None,
            parameters.s3_profile is not None))

    if parameters.logfile is None:
        fname = os.path.basename(__file__)
        fsplit = os.path.splitext(fname)
        if fsplit[1] == '.py':
            fname = fsplit[0]
        parameters.logfile = os.path.join(os.getcwd(), '{}.log'.format(fname))

    fh = logging.FileHandler(parameters.logfile)
    ch = logging.StreamHandler()

    formatter = logging.Formatter('%(asctime)s::%(name)s::%(processName)s'
                                  '::%(levelname)s::%(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    log_level_value = getattr(logging, parameters.loglevel)
    logger.setLevel(log_level_value)
    fh.setLevel(log_level_value)
    ch.setLevel(log_level_value)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info('Loglevel set to {}'.format(parameters.loglevel))

    if parameters.s3_infrequent_access and \
        ((parameters.retention_days is None and
         (parameters.retention_weeks is not None or
          parameters.retention_months is not None)) or
         (parameters.retention_days is not None and
          parameters.retention_days < 30)):
        logger.warning('You are using S3 Infrequent Access storage '
                       'with a retention policy (days) of less than '
                       '30 days. Be aware that Infrequent Access objects '
                       'are billed for at least 30 days, even if they are '
                       'deleted.')

    if parameters.command != 'common' and \
            parameters.command.lower() in dir(commands):
        mod = getattr(commands, parameters.command.lower())

        if parameters.command.capitalize() in dir(mod):
            command_class = getattr(mod, parameters.command.capitalize())

            if issubclass(command_class, commands.common.Command):
                command = command_class(parameters, const_parameters, logger)
                command.run()
                return

    parser_error('Command \'{}\' not found'.format(parameters.command))


def parse_args():
    global parser

    parser = argparse.ArgumentParser(
        description='DynamoDB backup\'n\'restore python script '
                    'with tarfile management')

    # Available commands
    parser.add_argument(
        'command',
        choices=(
            'backup',
            'restore',
            'check',
        ),
        help='The command to run')

    # Logging options
    parser.add_argument(
        '-d', '--debug',
        dest='loglevel',
        default='INFO',
        action='store_const', const='DEBUG',
        help='Activate debug output')
    parser.add_argument(
        '--loglevel',
        dest='loglevel',
        default='INFO',
        choices=('NOTSET', 'DEBUG', 'INFO',
                 'WARNING', 'ERROR', 'CRITICAL'),
        help='Define the specific log level')
    parser.add_argument(
        '--logfile',
        default=None,
        help='The path to the logfile')

    # AWS general options
    parser.add_argument(
        '--profile',
        default=os.getenv('AWS_DEFAULT_PROFILE', None),
        help='Define the AWS profile name (defaults to the environment '
             'variable AWS_DEFAULT_PROFILE)')
    parser.add_argument(
        '--access-key', '--accessKey',
        default=os.getenv('AWS_ACCESS_KEY_ID', None),
        help='Define the AWS default access key (defaults to the '
             'environment variable AWS_ACCESS_KEY_ID)')
    parser.add_argument(
        '--secret-key', '--secretKey',
        default=os.getenv('AWS_SECRET_ACCESS_KEY', None),
        help='Define the AWS default secret key (defaults to the '
             'environment variable AWS_SECRET_ACCESS_KEY)')
    parser.add_argument(
        '--region',
        default=os.getenv('AWS_DEFAULT_REGION', None),
        help='Define the AWS default region (defaults to the environment '
             'variable AWS_DEFAULT_REGION)')

    # AWS DynamoDB specific options
    parser.add_argument(
        '--ddb-profile',
        default=os.getenv('DDB_AWS_DEFAULT_PROFILE', None),
        help='Define the AWS DynamoDB profile name')
    parser.add_argument(
        '--ddb-access-key', '--ddb-accessKey',
        default=os.getenv('DDB_AWS_ACCESS_KEY_ID', None),
        help='Define the AWS DynamoDB access key')
    parser.add_argument(
        '--ddb-secret-key', '--ddb-secretKey',
        default=os.getenv('DDB_AWS_SECRET_ACCESS_KEY', None),
        help='Define the AWS DynamoDB secret key')
    parser.add_argument(
        '--ddb-region',
        default=os.getenv('DDB_AWS_DEFAULT_REGION', None),
        help='Define the AWS DynamoDB region')

    # AWS S3 specific options
    parser.add_argument(
        '--s3',
        action='store_true',
        help='Activate S3 mode')
    parser.add_argument(
        '--s3-server-side-encryption', '--s3-sse',
        action='store_true',
        help='Use server side encryption (AES256)')
    parser.add_argument(
        '--s3-infrequent-access', '--s3-ia',
        action='store_true',
        help='Store the objects in S3 as Infrequent Access '
             '(WARNING: IA objects are billed for at least 30 days)')
    parser.add_argument(
        '--s3-create-bucket',
        action='store_true',
        help='Create S3 bucket if it does not exist')
    parser.add_argument(
        '--s3-profile',
        default=os.getenv('S3_AWS_DEFAULT_PROFILE', None),
        help='Define the AWS S3 profile name')
    parser.add_argument(
        '--s3-access-key', '--s3-accessKey',
        default=os.getenv('S3_AWS_ACCESS_KEY_ID', None),
        help='Define the AWS S3 access key')
    parser.add_argument(
        '--s3-secret-key', '--s3-secretKey',
        default=os.getenv('S3_AWS_SECRET_ACCESS_KEY', None),
        help='Define the AWS S3 secret key')
    parser.add_argument(
        '--s3-region',
        default=os.getenv('S3_AWS_DEFAULT_REGION', None),
        help='Define the AWS S3 region')
    parser.add_argument(
        '--s3-bucket',
        default=os.getenv('S3_BUCKET', None),
        help='Define the AWS S3 bucket')

    # Backup and restore common options
    parser.add_argument(
        '--table',
        default='*',
        help='The table to backup or restore (\'*\' means all tables, '
             '\'t*\' means all tables starting with \'t\')')
    parser.add_argument(
        '--max-processes',
        default=multiprocessing.cpu_count() * 2,
        type=int,
        help='The maximum number of processes to run concurrently '
             '(one more process will be run to write to tar files)')

    # Dump path options
    parser.add_argument(
        '--dump-path',
        default=None,
        help='The path to the dump directory; if specified, neither '
             '--dump-dir nor --dump-format will be used')
    parser.add_argument(
        '--dump-dir',
        default=os.getcwd(),
        help='The path to the dump directory, in which the dump '
             'file/directory will be created, depending on the '
             '--dump-format')
    parser.add_argument(
        '--dump-format',
        default='dynamodb-dump-%Y%m%d%H%M%S.tgz',
        help='The format of the file (if tar extension provided) or '
             'directory used for the dump')

    # Backup specific options
    parser.add_argument(
        '--backup-only', '--only',
        default=None,
        choices=('data', 'schema'),
        help='To backup only the data or schema')
    parser.add_argument(
        '--retention-days',
        default=None,
        type=int,
        help='The retention policy for the backups, in the form of '
             '\'keep all backups for X days\'')
    parser.add_argument(
        '--retention-weeks',
        default=None,
        type=int,
        help='The retention policy for the backups, in the form of '
             '\'keep weekly backups for X weeks\'')
    parser.add_argument(
        '--retention-months',
        default=None,
        type=int,
        help='The retention policy for the backups, in the form of '
             '\'keep monthly backups for X months\'')
    parser.add_argument(
        '--sanity-check-action',
        default='raise',
        choices=('ignore', 'warning', 'raise'),
        help='The action to take when a sanity check threshold is reached, '
             'ignore just ignores it, warning warn about it and raise raise '
             'an exception')
    parser.add_argument(
        '--sanity-check-threshold-more',
        default=None,
        type=int,
        help='The threshold, in percent, for the number of elements that '
             'could be in excess of what is advertised by DynamoDB (keep '
             'in mind that there is an update every six hours)')
    parser.add_argument(
        '--sanity-check-threshold-less',
        default=None,
        type=int,
        help='The threshold, in percent, for the number of elements that '
             'could be in lack of what is advertised by DynamoDB (keep '
             'in mind that there is an update every six hours)')

    # Restore specific options
    parser.add_argument(
        '--restore-last',
        action='store_true',
        help='Restore the last available backup according to '
             'the dump format')
    parser.add_argument(
        '--restore-interactive',
        action='store_true',
        help='Enter the interactive mode to select which backup to '
             'restore')
    parser.add_argument(
        '--extract-before',
        action='store_true',
        help='Extract the tar file before proceeding to fasten the '
             'restore process')
    parser.add_argument(
        '--tmp-write-capacity',
        default=None,
        type=int,
        nargs='?',
        const=25,
        help='Set a temporary write capacity for the table if it\'s normal '
             'write capacity is lower, then reset it to the right value at '
             'the end of the restore process')
    parser.add_argument(
        '--ensure-matching-names',
        default='warning',
        choices=('ignore', 'warning', 'raise'),
        help='Ensure that the name of the directory in which a table is '
             'stored matches the name of that table in the stored schema. '
             'By default, a warning will emited.')

    # Parsing
    return parser.parse_args()


if __name__ == '__main__':
    cli()
