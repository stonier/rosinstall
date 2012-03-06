# Software License Agreement (BSD License)
#
# Copyright (c) 2010, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import print_function
import pkg_resources

import os
import config_yaml
from common import MultiProjectException, DistributedWork
from config import Config
from config_yaml import aggregate_from_uris


## The _cmd python files attempt to provide a reasonably
## complete level of abstraction to multiproject functionality.
## 
## Client code will need to pass the Config element through,
## and may use the ConfigElement API in places.
## There are no guarantees at this time for the API to
## remain stable, but the cmd API probably will change least.
## A change to expect is abstraction of user interaction.

import vcstools
from vcstools import VcsClient

def _select_element(elements, localname):
  """
  selects entry where path or localname matches.
  Prefers localname matches in case of ambiguity.
  """
  path_candidate = None
  if localname is not None:
    realpath = os.path.realpath(localname)
    for element in elements:
      if localname == element.get_local_name():
        path_candidate = element
        break
      elif realpath == os.path.realpath(element.get_path()):
        path_candidate = element
    if path_candidate == None:
      raise MultiProjectException("No config element matches; %s"%localname)
  return path_candidate

def get_config(basepath, additional_uris = None, config_filename = None):
  """
  Create a Config element necessary for all other commands.
  The command will look at the uris in sequence, each
  can be a web resource, a filename or a folder. In case it is
  a folder, when a config_filename is provided, the folder will
  be searched for a file of that name, and that one will be used.
  Else the folder will be considered a target location for the config.
  All files will be parsed for config elements, thus conceptually
  the input to Config is an expanded list of config elements. Config
  takes this list and consolidates duplicate paths by keeping the last
  one in the list.
  :param basepath: where relative paths shall be resolved against
  :param additional_uris: the location of config specifications or folders
  :param config_filename: name of files which may be looked at for config information
  :returns: a Config object
  :raises MultiProjectException: on plenty of errors
  """
  if basepath is None:
    raise MultiProjectException("Need to provide a basepath for Config.")
    
  
  # Find all the configuration sources
  if additional_uris is None and config_filename is not None:
    additional_uris = [os.path.join(basepath, config_filename)]

  if additional_uris is None:
    raise MultiProjectException("no source config file found!")

  path_specs = aggregate_from_uris(additional_uris, config_filename, basepath)

  ## Could not get uri therefore error out
  if len(path_specs) == 0:
    raise MultiProjectException("no source config files found at %s or %s"%(
        os.path.join(basepath, config_filename), additional_uris))

  #print("source...........................", path_specs)

  ## Generate the config class with the uri and path
  config = Config(path_specs, basepath, config_filename)

  return config


def cmd_persist_config(config, filename, header = None):
    """writes config to given file in yaml syntax"""
    config_yaml.generate_config_yaml(config, filename, header)

    
def cmd_version():
  """Returns extensive version information"""
  def prettyversion(vdict):
    version = vdict.pop("version")
    return "%s; %s"%(version, ",".join(vdict.values()) )
  return """vcstools:  %s
SVN:       %s
Mercurial: %s
Git:       %s
Tar:       %s
Bzr:       %s
"""%(pkg_resources.require("vcstools")[0].version,
     prettyversion(vcstools.SvnClient.get_environment_metadata()),
     prettyversion(vcstools.HgClient.get_environment_metadata()),
     prettyversion(vcstools.GitClient.get_environment_metadata()),
     prettyversion(vcstools.TarClient.get_environment_metadata()),
     prettyversion(vcstools.BzrClient.get_environment_metadata()))

def cmd_status(config, localname = None, untracked = False):
  """
  calls SCM status for all SCM entries in config, relative to path
  :returns: List of dict {element: ConfigElement, diff: diffstring}
  :param untracked: also show files not added to the SCM
  :raises MultiProjectException: on plenty of errors
  """
  class StatusRetriever():
    def __init__(self, element, path, untracked):
      self.element = element
      self.path = path
      self.untracked = untracked
    def do_work(self):
      path_spec = self.element.get_path_spec()
      scmtype = path_spec.get_scmtype()
      status = self.element.get_status(self.path, self.untracked)
      # align other scm output to svn
      columns = -1
      if scmtype == "git":
        columns = 3
      elif scmtype == "hg":
        columns = 2
      elif scmtype == "bzr":
        columns = 4
      if columns > -1 and status != None:
        status_aligned = ''
        for line in status.splitlines():
          status_aligned = status_aligned + line[:columns].ljust(8) + line[columns:] + '\n'
        status = status_aligned
      return {'status':status}

  path = config.get_base_path()
  # call SCM info in separate threads
  elements = config.get_config_elements()
  work = DistributedWork(len(elements))
  selected_element = _select_element(elements, localname)
  if selected_element is not None:
    work.add_thread(StatusRetriever(selected_element, path, untracked))
  else:
    for element in elements:
      if element.is_vcs_element():
        work.add_thread(StatusRetriever(element, path, untracked))
  outputs = work.run()
  return outputs


def cmd_diff(config, localname = None):
  """
  calls SCM diff for all SCM entries in config, relative to path
  :returns: List of dict {element: ConfigElement, diff: diffstring}
  :raises MultiProjectException: on plenty of errors
  """
  class DiffRetriever():
    def __init__(self, element, path):
      self.element = element
      self.path = path
    def do_work(self):
      return {'diff':self.element.get_diff(self.path)}

  path = config.get_base_path()
  elements = config.get_config_elements()
  work = DistributedWork(len(elements))
  selected_element = _select_element(elements, localname)
  if selected_element is not None:
    work.add_thread(DiffRetriever(selected_element, path))
  else:
    for element in elements:
      if element.is_vcs_element():
        work.add_thread(DiffRetriever(element, path))
  outputs = work.run()
  return outputs

def cmd_install_or_update(config, backup_path = None, mode = 'abort', robust = False):
  """
  performs many things, generally attempting to make
  the local filesystem look like what the config specifies,
  pulling from remote sources the most recent changes.
  
  The command may have stdin user interaction (TODO abstract)
  :param backup_path: if and where to backup trees before deleting them
  :param robust: proceed to next element even when one element fails
  :returns: True on Success
  :raises MultiProjectException: on plenty of errors
  """
  success = True
  if not os.path.exists(config.get_base_path()):
    os.mkdir(config.get_base_path())
  # Prepare install operation check filesystem and ask user
  preparation_reports = []
  for t in config.get_config_elements():
    abs_backup_path = None
    if backup_path is not None:
      abs_backup_path = os.path.join(config.get_base_path(), backup_path)
    try:
      preparation_report = t.prepare_install(backup_path = abs_backup_path, arg_mode = mode, robust = robust)
      if preparation_report is not None:
        if preparation_report.abort:
          raise MultiProjectException("Aborting install because of %s"%preparation_report.error)
        if not preparation_report.skip:
          preparation_reports.append(preparation_report)
        else:
          print("Skipping install of %s because: %s"%(preparation_report.config_element.get_local_name(),
                                                      preparation_report.error))
    except MultiProjectException as ex:
      fail_str = "Failed to install tree '%s'\n %s"%(t.get_path(), ex)
      if robust:
        success = False
        print("Continuing despite %s"%fail_str)
      else:
        raise MultiProjectException(fail_str)
      
  class Installer():
    def __init__(self, report):
      self.element = report.config_element
      self.report = report
    def do_work(self):
      self.element.install(self.report.checkout, self.report.backup, self.report.backup_path)
      return {}

  work = DistributedWork(len(preparation_reports))
  for report in preparation_reports:
    thread = Installer(report)
    work.add_thread(thread)
 
  try:
    outputs = work.run()
  except MultiProjectException as e:  
    success = False
    if robust:
      print("Errors during install %s"%(e))
    else:
      raise e
  return success
  # TODO go back and make sure that everything in options.path is described
  # in the yaml, and offer to delete otherwise? not sure, but it could go here
