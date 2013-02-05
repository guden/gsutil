# Copyright 2013 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contains gsutil base integration test case class."""

import logging
import os.path
import random
import shutil
import subprocess
import sys
import tempfile

import boto
from boto.exception import GSResponseError

from gslib.project_id import ProjectIdHandler
import gslib.tests.util as util
from gslib.tests.util import Retry
from gslib.tests.util import unittest
from gslib.util import IS_WINDOWS
import base


CURDIR = os.path.abspath(os.path.dirname(__file__))
TESTS_DIR = os.path.split(CURDIR)[0]
GSLIB_DIR = os.path.split(TESTS_DIR)[0]
GSUTIL_DIR = os.path.split(GSLIB_DIR)[0]
GSUTIL_PATH = os.path.join(GSUTIL_DIR, 'gsutil')
MAX_BUCKET_LENGTH = 63
LOGGER = logging.getLogger('integration-test')


@unittest.skipUnless(util.RUN_INTEGRATION_TESTS,
                     'Not running integration tests.')
class GsUtilIntegrationTestCase(base.GsUtilTestCase):
  """Base class for gsutil integration tests."""

  def setUp(self):
    self.bucket_uris = []
    self.tempdirs = []

    # Set up API version and project ID handler.
    self.api_version = boto.config.get_value(
        'GSUtil', 'default_api_version', '1')
    self.proj_id_handler = ProjectIdHandler()

  # Retry with an exponential backoff if a server error is received. This
  # ensures that we try *really* hard to clean up after ourselves.
  @Retry(GSResponseError, logger=LOGGER)
  def tearDown(self):
    while self.tempdirs:
      tmpdir = self.tempdirs.pop()
      shutil.rmtree(tmpdir, ignore_errors=True)

    while self.bucket_uris:
      bucket_uri = self.bucket_uris[-1]
      bucket_list = list(bucket_uri.list_bucket(all_versions=True))
      while bucket_list:
        for k in bucket_list:
          k.delete()
        bucket_list = list(bucket_uri.list_bucket(all_versions=True))
      bucket_uri.delete_bucket()
      self.bucket_uris.pop()

  def MakeTempName(self, kind):
    """Creates a temporary name that is most-likely unique.

    Args:
      kind: A string indicating what kind of test name this is.

    Returns:
      The temporary name.
    """
    name = 'gsutil-test-%s-%s' % (self._testMethodName, kind)
    name = name[:MAX_BUCKET_LENGTH-9]
    name = '%s-%08x' % (name, random.randrange(256**4))
    return name

  def CreateBucket(self, bucket_name=None, test_objects=0, storage_class=None):
    """Creates a test bucket.

    The bucket and all of its contents will be deleted after the test.

    Args:
      bucket_name: Create the bucket with this name. If not provided, a
                   temporary test bucket name is constructed.
      test_objects: The number of objects that should be placed in the bucket.
                    Defaults to 0.
      storage_class: storage class to use. If not provided we us standard.

    Returns:
      StorageUri for the created bucket.
    """
    bucket_name = bucket_name or self.MakeTempName('bucket')

    bucket_uri = boto.storage_uri('gs://%s' % bucket_name.lower(),
                                  suppress_consec_slashes=False)

    # Apply API version and project ID headers if necessary.
    headers = {'x-goog-api-version': self.api_version}
    self.proj_id_handler.FillInProjectHeaderIfNeeded('test', bucket_uri, headers)

    bucket_uri.create_bucket(storage_class=storage_class)
    self.bucket_uris.append(bucket_uri)
    for i in range(test_objects):
      self.CreateObject(bucket_uri=bucket_uri,
                        object_name=self.MakeTempName('obj'),
                        contents='test %d' % i)
    return bucket_uri

  def CreateVersionedBucket(self, bucket_name=None, test_objects=0):
    """Creates a versioned test bucket.

    The bucket and all of its contents will be deleted after the test.

    Args:
      bucket_name: Create the bucket with this name. If not provided, a
                   temporary test bucket name is constructed.
      test_objects: The number of objects that should be placed in the bucket.
                    Defaults to 0.

    Returns:
      StorageUri for the created bucket with versioning enabled.
    """
    bucket_uri = self.CreateBucket(bucket_name=bucket_name,
                               test_objects=test_objects)
    bucket_uri.configure_versioning(True)
    return bucket_uri

  def CreateObject(self, bucket_uri=None, object_name=None, contents=None):
    """Creates a test object.

    Args:
      bucket: The URI of the bucket to place the object in. If not specified, a
              new temporary bucket is created.
      object_name: The name to use for the object. If not specified, a temporary
                   test object name is constructed.
      contents: The contents to write to the object. If not specified, the key
                is not written to, which means that it isn't actually created
                yet on the server.

    Returns:
      A StorageUri for the created object.
    """
    bucket_uri = bucket_uri or self.CreateBucket()
    object_name = object_name or self.MakeTempName('obj')
    key_uri = bucket_uri.clone_replace_name(object_name)
    if contents is not None:
      key_uri.set_contents_from_string(contents)
    return key_uri

  def CreateTempDir(self):
    """Creates a temporary directory on disk.

    The directory and all of its contents will be deleted after the test.

    Returns:
      The path to the new temporary directory.
    """
    tmpdir = tempfile.mkdtemp(prefix=self.MakeTempName('directory'))
    self.tempdirs.append(tmpdir)
    return tmpdir

  def CreateTempFile(self, tmpdir=None, contents=None):
    """Creates a temporary file on disk.

    Args:
      tmpdir: The temporary directory to place the file in. If not specified, a
              new temporary directory is created.
      contents: The contents to write to the file. If not specified, a test
                string is constructed and written to the file.

    Returns:
      The path to the new temporary file.
    """
    tmpdir = tmpdir or self.CreateTempDir()
    fpath = os.path.join(tmpdir, self.MakeTempName('file'))
    with open(fpath, 'w') as f:
      contents = contents or self.MakeTempName('contents')
      f.write(contents)
    return fpath

  def RunGsUtil(self, cmd, return_status=False, return_stdout=False,
                return_stderr=False, expected_status=0, stdin=None):
    """Runs the gsutil command.

    Args:
      cmd: The command to run, as a list, e.g. ['cp', 'foo', 'bar']
      return_status: If True, the exit status code is returned.
      return_stdout: If True, the standard output of the command is returned.
      return_stderr: If True, the standard error of the command is returned.
      expected_status: The expected return code. If not specified, defaults to
                       0. If the return code is a different value, an exception
                       is raised.
      stdin: A string of data to pipe to the process as standard input.

    Returns:
      A tuple containing the desired return values specified by the return_*
      arguments.
    """
    cmd = [GSUTIL_PATH] + cmd
    if IS_WINDOWS:
      cmd = [sys.executable] + cmd
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.PIPE)
    (stdout, stderr) = p.communicate(stdin)
    status = p.returncode

    if expected_status is not None:
      self.assertEqual(
          status, expected_status,
          msg='Expected status %d, got %d.\nCommand:\n%s\n\nstderr:\n%s' % (
              expected_status, status, ' '.join(cmd), stderr))

    toreturn = []
    if return_status:
      toreturn.append(status)
    if return_stdout:
      toreturn.append(stdout)
    if return_stderr:
      toreturn.append(stderr)

    if len(toreturn) == 1:
      return toreturn[0]
    elif toreturn:
      return tuple(toreturn)
