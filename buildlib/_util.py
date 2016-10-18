# ungoogled-chromium: A Google Chromium variant for removing Google integration and
# enhancing privacy, control, and transparency
# Copyright (C) 2016  Eloston
#
# This file is part of ungoogled-chromium.
#
# ungoogled-chromium is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ungoogled-chromium is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ungoogled-chromium.  If not, see <http://www.gnu.org/licenses/>.

'''Miscellaneous utilities'''

import logging
import configparser
import pathlib
import tarfile
import urllib.request
import shutil
import os
import subprocess

class BuilderException(Exception):
    '''buildlib Builder exception for distinguishing errors'''

    pass

def get_default_logger():
    '''Gets the default logger'''

    logger = logging.getLogger("ungoogled_chromium")
    logger.setLevel(logging.DEBUG)

    if not logger.hasHandlers():
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s: %(message)s")
        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.info("Initialized default console logging handler")
    return logger

def parse_version_ini(version_configfile, chromium_version, release_revision):
    '''Parse version.ini to fill in missing values'''

    if chromium_version is None or release_revision is None:
        version_config = configparser.ConfigParser()
        version_config.read(str(version_configfile))
    if chromium_version is None:
        chromium_version = version_config["main"]["chromium_version"]
    if release_revision is None:
        release_revision = version_config["main"]["release_revision"]
    return chromium_version, release_revision

def safe_create_dir(logger, dir_path):
    '''Safely creates an empty directory'''

    if dir_path.exists():
        if not dir_path.is_dir():
            raise BuilderException("Path {!s} already exists, "
                                   "but is not a directory".format(dir_path))
    else:
        logger.info("Directory {!s} does not exist. Creating...".format(dir_path))
        dir_path.mkdir()
    return dir_path

def extract_tar_file(logger, tar_path, destination_dir, ignore_files, relative_to):
    '''Improved one-time tar extraction function'''

    class NoAppendList(list):
        '''Hack to workaround memory issues with large tar files'''

        def append(self, obj):
            pass

    # Simple hack to check if symlinks are supported
    try:
        os.symlink("", "")
    except FileNotFoundError:
        # Symlinks probably supported
        symlink_supported = True
    except OSError:
        # Symlinks probably not supported
        logger.warning("Symlinks not supported. Will ignore all symlinks")
        symlink_supported = False
    except Exception as exc:
        # Unexpected exception
        raise exc

    with tarfile.open(str(tar_path)) as tar_file_obj:
        tar_file_obj.members = NoAppendList()
        for tarinfo in tar_file_obj:
            try:
                if relative_to is None:
                    relative_path = pathlib.PurePosixPath(tarinfo.name)
                else:
                    relative_path = pathlib.PurePosixPath(tarinfo.name).relative_to(relative_to) # pylint: disable=redefined-variable-type
                if str(relative_path) in ignore_files:
                    ignore_files.remove(str(relative_path))
                else:
                    destination = destination_dir / pathlib.Path(*relative_path.parts)
                    if tarinfo.issym() and not symlink_supported:
                        # In this situation, TarFile.makelink() will try to create a copy of the
                        # target. But this fails because TarFile.members is empty
                        # But if symlinks are not supported, it's safe to assume that symlinks
                        # aren't needed. The only situation where this happens is on Windows.
                        continue
                    if tarinfo.islnk():
                        # Derived from TarFile.extract()
                        relative_target = pathlib.PurePosixPath(
                            tarinfo.linkname).relative_to(relative_to)
                        tarinfo._link_target = str( # pylint: disable=protected-access
                            destination_dir / pathlib.Path(*relative_target.parts))
                    if destination.is_symlink():
                        destination.unlink()
                    tar_file_obj._extract_member(tarinfo, str(destination)) # pylint: disable=protected-access
            except Exception as exc:
                logger.error("Exception thrown for tar member {}".format(tarinfo.name))
                raise exc

def domain_substitute(logger, regex_list, file_list, log_warnings=True):
    '''Runs domain substitution with regex_list over files file_list'''

    for path in file_list:
        try:
            with path.open(mode="r+b") as file_obj:
                content = file_obj.read()
                file_subs = 0
                for regex_pair in regex_list:
                    compiled_regex, replacement_regex = regex_pair
                    content, number_of_subs = compiled_regex.subn(replacement_regex, content)
                    file_subs += number_of_subs
                if file_subs > 0:
                    file_obj.seek(0)
                    file_obj.write(content)
                    file_obj.truncate()
                elif log_warnings:
                    logger.warning("File {} has no matches".format(path))
        except Exception as exc:
            logger.error("Exception thrown for path {}".format(path))
            raise exc

def download_if_needed(logger, file_path, url, force_download):
    '''Downloads a file if necessary, unless force_download is True'''

    if file_path.exists() and not file_path.is_file():
        raise BuilderException("{} is an existing non-file".format(str(file_path)))
    elif force_download or not file_path.is_file():
        logger.info("Downloading {} ...".format(str(file_path)))
        with urllib.request.urlopen(url) as response:
            with file_path.open("wb") as file_obj:
                shutil.copyfileobj(response, file_obj)
    else:
        logger.info("{} already exists. Skipping download.".format(str(file_path)))

# This is a port from https://hg.python.org/cpython/file/3.5/Lib/subprocess.py#l629
# TODO: Delete this when Python 3.5 is required
class _CompletedProcess(object): # pylint: disable=too-few-public-methods
    """A process that has finished running.

    This is returned by run().

    Attributes:
      args: The list or str args passed to run().
      returncode: The exit code of the process, negative for signals.
      stdout: The standard output (None if not captured).
      stderr: The standard error (None if not captured).
    """
    def __init__(self, args, returncode, stdout=None, stderr=None):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self):
        args = ['args={!r}'.format(self.args),
                'returncode={!r}'.format(self.returncode)]
        if self.stdout is not None:
            args.append('stdout={!r}'.format(self.stdout))
        if self.stderr is not None:
            args.append('stderr={!r}'.format(self.stderr))
        return "{}({})".format(type(self).__name__, ', '.join(args))

    def check_returncode(self):
        """Raise CalledProcessError if the exit code is non-zero."""
        if self.returncode:
            raise subprocess.CalledProcessError(self.returncode, self.args, self.stdout)

# This is a port of run() from https://hg.python.org/cpython/file/3.5/Lib/subprocess.py#l662
# TODO: Delete this when Python 3.5 is required
def subprocess_run(*popenargs, input=None, timeout=None, check=False, **kwargs): # pylint: disable=redefined-builtin
    """Run command with arguments and return a CompletedProcess instance.

    The returned instance will have attributes args, returncode, stdout and
    stderr. By default, stdout and stderr are not captured, and those attributes
    will be None. Pass stdout=PIPE and/or stderr=PIPE in order to capture them.

    If check is True and the exit code was non-zero, it raises a
    CalledProcessError. The CalledProcessError object will have the return code
    in the returncode attribute, and output & stderr attributes if those streams
    were captured.

    If timeout is given, and the process takes too long, a TimeoutExpired
    exception will be raised.

    There is an optional argument "input", allowing you to
    pass a string to the subprocess's stdin.  If you use this argument
    you may not also use the Popen constructor's "stdin" argument, as
    it will be used internally.

    The other arguments are the same as for the Popen constructor.

    If universal_newlines=True is passed, the "input" argument must be a
    string and stdout/stderr in the returned object will be strings rather than
    bytes.
    """
    if input is not None:
        if 'stdin' in kwargs:
            raise ValueError('stdin and input arguments may not both be used.')
        kwargs['stdin'] = subprocess.PIPE

    with subprocess.Popen(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(process.args, timeout, output=stdout,
                                            stderr=stderr)
        except:
            process.kill()
            process.wait()
            raise
        retcode = process.poll()
        if check and retcode:
            raise subprocess.CalledProcessError(retcode, process.args,
                                                output=stdout)
    return _CompletedProcess(process.args, retcode, stdout, stderr)
