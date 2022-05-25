#!/usr/bin/env python

"""Prints performance result test data from test runs captured in Apple .xcresult bundles.

USAGE: xcresult_extract.py -project <path> -scheme <scheme> [other flags...]

xcresult_extract.py finds and displays the log output associated with an xcodebuild
invocation. Pass your entire xcodebuild command-line as arguments to this script
and it will find the output associated with the most recent invocation.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys

from lib import command_trace

_logger = logging.getLogger('xcresult')


def main():
  args = sys.argv[1:]
  if not args:
    sys.stdout.write(__doc__)
    sys.exit(1)

  logging.basicConfig(format='%(message)s', level=logging.DEBUG)

  flags = parse_xcodebuild_flags(args)

  # If the result bundle path is specified in the xcodebuild flags, use that
  # otherwise, deduce
  project = project_from_project_path(flags['-project'])
  scheme = flags['-scheme']
  defaultValues = ["testName", "Duration", "Disk Local Writes", "Clock Monotonic Time", "CPU Time", "Memory Physical", "CPU Instructions Retired", "Memory Peak Physical", "CPU Cycles"]

  xcresult_path = flags.get('-resultBundlePath')
  if xcresult_path is None:
    xcresult_path = find_xcresult_path(project, scheme)
  
  test_id = find_test_id(xcresult_path)
  tests_count = find_test_count(xcresult_path)
  print("Number of Tests=" + tests_count)
  listData = []

  summary_id = find_summary_id(xcresult_path, test_id, int(tests_count))
  for sumID in range(len(summary_id)):
    log = export_log(xcresult_path, summary_id[sumID])
    device_info = find_device_info(xcresult_path)
    #sys.stdout.write("Device: " + device_info + "\n")
    #sys.stdout.write("Found metrics: " + log + "\n")
    sys.stdout.write(device_info + "," + log + "\n")

    data = log.split(",")

    createDict = zip(defaultValues, data)
    dictOfData = dict(createDict)
    print(dictOfData)
    listData.append(dictOfData)

  writeDataToFile(listData)
  ''' This would be the result:
  {'testName': 'TabsPerformanceTest/testPerfTabs1280startup()', 'cpu': '177.34', 'memory': '607491.7616000001', 'cycles': '21.7424204788', 'blabla': '0.20137628960000004', 'foo': '68.8128', 'bar': '265755.0654', 'foobar': '0.0', 'zoo': '0.0'}
  '''


# Most flags on the xcodebuild command-line are uninteresting, so only pull
# flags with known behavior with names in this set.
INTERESTING_FLAGS = {
    '-resultBundlePath',
    '-scheme',
    '-project',
}

def writeDataToFile(data):
  f = open( 'data.txt', 'w' )
  f.write(str(data))
  f.close()

def parse_xcodebuild_flags(args):
  """Parses the xcodebuild command-line.

  Extracts flags like -workspace and -scheme that dictate the location of the
  logs.
  """
  result = {}
  key = None
  for arg in args:
    if arg.startswith('-'):
      if arg in INTERESTING_FLAGS:
        key = arg
    elif key is not None:
      result[key] = arg
      key = None

  return result


def project_from_project_path(path):
  """Extracts the project name from a project path.
  Args:
    path: The path to a .xcodeproj file

  Returns:
    The project name from the basename of the path. For example, if path were
    'Client/Example/Client.xcodeproj', returns 'Client'.
  """
  root, ext = os.path.splitext(os.path.basename(path))
  if ext == '.xcodeproj':
    _logger.debug('Using project %s from project %s', root, path)
    return root

  raise ValueError('%s is not a valid project path' % path)


def find_xcresult_path(project, scheme):
  """Finds an xcresult bundle for the given project and scheme.

  Args:
    project: The project name, like 'Client'
    scheme: The Xcode scheme that was tested

  Returns:
    The path to the newest xcresult bundle that matches.
  """
  project_path = find_project_path(project)
  bundle_dir = os.path.join(project_path, 'Logs/Test')
  prefix = re.compile('([^-]*)-' + re.escape(scheme) + '-')

  _logger.debug('Logging for xcresult bundles in %s', bundle_dir)
  xcresult = find_newest_matching_prefix(bundle_dir, prefix)
  if xcresult is None:
    raise LookupError(
        'Could not find xcresult bundle for %s in %s' % (scheme, bundle_dir))

  _logger.debug('Found xcresult: %s', xcresult)
  return xcresult


def find_project_path(project):
  """Finds the newest project output within Xcode's DerivedData.

  Args:
    project: A project name; the Foo in Foo.xcworkspace

  Returns:
    The path containing the newest project output.
  """
  path = os.path.expanduser('~/Library/Developer/Xcode/DerivedData')
  prefix = re.compile(re.escape(project) + '-')

  # DerivedData has directories like Client-csljdukzqbozahdjizcvrfiufrkb. Use
  # the most recent one if there are more than one such directory matching the
  # project name.
  result = find_newest_matching_prefix(path, prefix)
  if result is None:
    raise LookupError(
        'Could not find project derived data for %s in %s' % (project, path))

  _logger.debug('Using project derived data in %s', result)
  return result


def find_newest_matching_prefix(path, prefix):
  """Lists the given directory and returns the newest entry matching prefix.

  Args:
    path: A directory to list
    prefix: A regular expression that matches the filenames to consider

  Returns:
    The path to the newest entry in the directory whose basename starts with
    the prefix.
  """
  entries = os.listdir(path)
  result = None
  for entry in entries:
    if prefix.match(entry):
      fq_entry = os.path.join(path, entry)
      if result is None:
        result = fq_entry
      else:
        result_mtime = os.path.getmtime(result)
        entry_mtime = os.path.getmtime(fq_entry)
        if entry_mtime > result_mtime:
          result = fq_entry

  return result


def find_legacy_log_files(xcresult_path):
  """Finds the log files produced by Xcode 10 and below."""

  result = []

  for root, dirs, files in os.walk(xcresult_path, topdown=True):
    for file in files:
      if file.endswith('.txt'):
        file = os.path.join(root, file)
        result.append(file)

  # Sort the files by creation time.
  result.sort(key=lambda f: os.stat(f).st_ctime)
  return result


def cat_files(files, output):
  """Reads the contents of all the files and copies them to the output.

  Args:
    files: A list of filenames
    output: A file-like object in which all the data should be copied.
  """
  for file in files:
    with open(file, 'r') as fd:
      shutil.copyfileobj(fd, output)

def find_device_info(xcresult_path):
  """Prints information about the device from an xcresult bundle.

  Args:
    xcresult_path: The path to an xcresult bundle.

  Returns:
    A string of device information from the provided xcresult bundle.
  """
  parsed = xcresulttool_json('get', '--path', xcresult_path)
  actions = parsed['actions']['_values']
  action = actions[-1]

  result = action['runDestination']['targetDeviceRecord']['modelUTI']['_value']
  return result

def find_test_count(xcresult_path):
  """ Finds the total subtest count.

  Args:
    xcresult_path: The path to an xcresult bundle.
    
  Returns:
    The total subtest count
  """
  parsed = xcresulttool_json('get', '--path', xcresult_path)

  result = parsed['metrics']['testsCount']['_value']
  _logger.debug('Using subtest count: %s', result)

  return result

def find_test_id(xcresult_path):
  """Finds the id of the last action's tests.

  Args:
    xcresult_path: The path to an xcresult bundle.

  Returns:
    The id of the test output, suitable for use with xcresulttool get --id.
  """
  parsed = xcresulttool_json('get', '--path', xcresult_path)
  actions = parsed['actions']['_values']
  action = actions[-1]

  result = action['actionResult']['testsRef']['id']['_value']
  _logger.debug('Using test id %s', result)
  return result

def find_summary_id(xcresult_path, test_id, tests_count):
  """Finds the id summary of the last action's tests.

  Args:
    xcresult_path: The path to an xcresult bundle.
    test_id: The id of the test output, suitable for use with xcresulttool get --id.
    sub_test: The test number

  Returns:
    The summary id of the test output, suitable for use with xcresulttool get --id.
  """
  parsed = xcresulttool_json('get', '--path', xcresult_path, '--id', test_id)
  actions = parsed['summaries']['_values']
  action = actions[0]
  resultDef = []

  for ids in range(tests_count):
      result = action['testableSummaries']['_values'][0]['tests']['_values'][0]['subtests']['_values'][0]['subtests']['_values'][0]['subtests']['_values'][ids]['summaryRef']['id']['_value']
      resultDef.append(result)
  '''
  When tests are run from xcode UI (play button), the performance test suite is the 40th. We will always launch from command line
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[40].subtests._values[1].summaryRef.id._value
  When tests are in several test suites...Two iterations, test suite and tests in that test suite, the total test count does not work as it is
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[1].subtests._values[0].summaryRef
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[0].subtests._values[0].summaryRef
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[1].subtests._values[0].summaryRef
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[1].subtests._values[1].summaryRef
  x.summaries._values[0].testableSummaries._values[0].tests._values[0].subtests._values[0].subtests._values[1].subtests._values[2].summaryRef
  '''
  #_logger.debug('Using summay test id %s', result)
  print(resultDef)
  return resultDef


def export_log(xcresult_path, summary_id):
  """Exports the log data with the given id from the xcresult bundle.

  Args:
    xcresult_path: The path to an xcresult bundle.
    summary_id: The id that names the log output (obtained by find_summary_id)

  Returns:
    The logged output, as a string.
  """
  contents = xcresulttool_json('get', '--path', xcresult_path, '--id', summary_id)

  result = []
  collect_log_output(contents, result)
  return ','.join(result)


def collect_log_output(activity_log, result):
  """ Collects emitted output from the activity log.

  Args:
    activity_log: Parsed JSON of an xcresult activity log.
    result: An array into which all log data should be appended.
  """

  test_name = activity_log.get('identifier')
  if test_name:
    result.append(test_name['_value'])

  duration = activity_log.get('duration')
  if duration:
    output = str("{:.2f}".format(float(duration['_value'])))
    result.append(output)

  performance_metrics = activity_log.get('performanceMetrics')
  if not performance_metrics is None:
    metrics = performance_metrics.get('_values')
    for metric in metrics:
      measurement = metric.get('measurements')
      values = measurement.get('_values')
      value_sum = 0
      for value in values:
        value_sum += float(value.get('_value'))
      output = str(value_sum / len(values))
      result.append(output)
 
def xcresulttool(*args):
  """Runs xcresulttool and returns its output as a string."""
  cmd = ['xcrun', 'xcresulttool']
  cmd.extend(args)

  command_trace.log(cmd)

  return subprocess.check_output(cmd)

def xcresulttool_json(*args):
  """Runs xcresulttool and its output as parsed JSON."""
  args = list(args) + ['--format', 'json']
  contents = xcresulttool(*args)
  return json.loads(contents)

if __name__ == '__main__':
  main()