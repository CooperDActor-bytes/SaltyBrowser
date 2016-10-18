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

'''Common code for all Builders'''

import hashlib
import pathlib
import re
import subprocess
import configparser
import distutils.dir_util
import os
import enum
import shutil

from . import _util
from ._util import BuilderException

CLEANING_LIST = pathlib.Path("cleaning_list")
DOMAIN_REGEX_LIST = pathlib.Path("domain_regex_list")
DOMAIN_SUBSTITUTION_LIST = pathlib.Path("domain_substitution_list")
PATCHES = pathlib.Path("patches")
EXTRA_DEPS = pathlib.Path("extra_deps.ini")
PATCH_ORDER = pathlib.Path("patch_order")

GYP_FLAGS = pathlib.Path("gyp_flags")
GN_ARGS = pathlib.Path("gn_args.ini")

class CPUArch(enum.Enum):
    '''
    Enum for CPU architectures
    '''
    x86 = "x86"
    x64 = "x64"

class Builder:
    '''
    Generic builder class. Also a metaclass for specific Builder implementations
    '''

    # pylint: disable=too-many-instance-attributes

    _resources = pathlib.Path("resources", "common")

    # Force the downloading of dependencies instead of checking if they exist
    force_download = False

    # Switch for running source cleaning
    run_source_cleaner = True

    # Switch for running domain substitution
    run_domain_substitution = True

    # The command to invoke Python 2
    # If set to none, the shebang line or file associations are used
    python2_command = None

    # The command to invoke ninja
    ninja_command = "ninja"

    # The build directory relative to the build sandbox
    build_output = pathlib.Path("out", "Release")

    # The ninja targets to build
    build_targets = ["chrome"]

    # The CPU architecture to build for. Set to None to let the meta-build configuration decide
    target_arch = None

    @staticmethod
    def _run_subprocess(*args, append_environ=None, **kwargs):
        if append_environ is None:
            return _util.subprocess_run(*args, **kwargs)
        else:
            new_env = dict(os.environ)
            new_env.update(append_environ)
            return _util.subprocess_run(*args, env=new_env, **kwargs)

    def __init__(self, version_configfile=pathlib.Path("version.ini"), chromium_version=None,
                 release_revision=None, build_dir=pathlib.Path("build"), logger=None):
        # pylint: disable=too-many-arguments
        if logger is None:
            self.logger = _util.get_default_logger()
        else:
            self.logger = logger
        self.logger.info("Using builder {!s}".format(type(self).__name__))

        self.chromium_version, self.release_revision = _util.parse_version_ini(
            version_configfile, chromium_version, release_revision)

        self.build_dir = _util.safe_create_dir(self.logger, build_dir)
        self._sandbox_dir = _util.safe_create_dir(self.logger, build_dir / pathlib.Path("sandbox"))
        self._downloads_dir = _util.safe_create_dir(self.logger,
                                                    build_dir / pathlib.Path("downloads"))
        self._path_overrides = _util.safe_create_dir(self.logger,
                                                     build_dir / pathlib.Path("path_overrides"))

        self._domain_regex_cache = None

    @classmethod
    def _resource_path_generator(cls, file_path):
        builder_order = list(cls.__mro__)
        if not builder_order.pop() is object:
            raise BuilderException("Last class of __mro__ is not object")
        builder_order.reverse()
        known_resources = set()
        for builder_type in builder_order:
            resource_path = builder_type._resources / file_path # pylint: disable=protected-access
            if not builder_type._resources in known_resources: # pylint: disable=protected-access
                known_resources.add(builder_type._resources) # pylint: disable=protected-access
                if resource_path.exists():
                    yield resource_path

    def _read_list_resource(self, file_name, is_binary=False):
        if is_binary:
            file_mode = "rb"
        else:
            file_mode = "r"
        tmp_list = list()
        for resource_path in self._resource_path_generator(file_name):
            self.logger.debug("Appending {!s}".format(resource_path))
            with resource_path.open(file_mode) as file_obj:
                tmp_list.extend(file_obj.read().splitlines())
        return [x for x in tmp_list if len(x) > 0]

    def _read_ini_resource(self, file_name):
        combined_dict = dict()
        for resource_ini in self._resource_path_generator(file_name):
            self.logger.debug("Including {!s}".format(resource_ini))
            resource_config = configparser.ConfigParser()
            resource_config.read(str(resource_ini))
            for section in resource_config:
                if section == "DEFAULT":
                    continue
                combined_dict[section] = dict()
                for config_key in resource_config[section]:
                    combined_dict[section][config_key] = resource_config[section][config_key]
        return combined_dict

    def _setup_tar_dependency(self, tar_url, tar_filename, strip_tar_dirs, dep_destination):
        tar_destination = self._downloads_dir / pathlib.Path(tar_filename)
        _util.download_if_needed(self.logger, tar_destination, tar_url, self.force_download)
        self.logger.info("Extracting {}...".format(tar_filename))
        os.makedirs(str(self._sandbox_dir / dep_destination), exist_ok=True)
        _util.extract_tar_file(self.logger, tar_destination, (self._sandbox_dir / dep_destination),
                               list(), strip_tar_dirs)

    def _get_parsed_domain_regexes(self):
        if self._domain_regex_cache is None:
            self._domain_regex_cache = list()
            for expression in self._read_list_resource(DOMAIN_REGEX_LIST, is_binary=True):
                expression = expression.split(b'#')
                self._domain_regex_cache.append((re.compile(expression[0]), expression[1]))
        return self._domain_regex_cache

    def _generate_patches(self):
        new_patch_order = str()
        for patch_order_path in self._resource_path_generator(PATCHES / PATCH_ORDER):
            self.logger.debug("Appending {!s}".format(patch_order_path))
            with patch_order_path.open() as file_obj:
                new_patch_order += file_obj.read()

            distutils.dir_util.copy_tree(str(patch_order_path.parent),
                                         str(self.build_dir / PATCHES))
            (self.build_dir / PATCHES / PATCH_ORDER).unlink()
        with (self.build_dir / PATCHES / PATCH_ORDER).open("w") as file_obj:
            file_obj.write(new_patch_order)

        if self.run_domain_substitution:
            self.logger.debug("Running domain substitution over patches...")
            _util.domain_substitute(self.logger, self._get_parsed_domain_regexes(),
                                    (self.build_dir / PATCHES).rglob("*.patch"),
                                    log_warnings=False)

    def _run_ninja(self, output, targets):
        # TODO: Use iterable unpacking instead when requiring Python 3.5
        result = self._run_subprocess([self.ninja_command, "-C", str(output)] + targets,
                                      cwd=str(self._sandbox_dir))
        if not result.returncode == 0:
            raise BuilderException("ninja returned non-zero exit code: {}".format(
                result.returncode))

    def check_build_environment(self):
        '''Checks the build environment before building'''

        self.logger.info("Checking Python 2 command...")
        if self.python2_command is None:
            # If None, probably using the shebang line which uses "python"
            self.logger.info("No Python 2 command specified; testing with 'python'")
            python_test_command = "python"
        else:
            python_test_command = self.python2_command
        result = self._run_subprocess([python_test_command, "-c",
                                       ("import sys;print('{}.{}.{}'.format("
                                        "sys.version_info.major, sys.version_info.minor, "
                                        "sys.version_info.micro))")],
                                      stdout=subprocess.PIPE, universal_newlines=True)
        if not result.returncode is 0:
            raise BuilderException("Python 2 command returned non-zero exit code {}".format(
                result.returncode))
        if not result.stdout.split(".")[0] is "2":
            raise BuilderException("Unsupported Python version '{!s}'".format(
                result.stdout.strip("\n")))
        self.logger.debug("Using Python version '{!s}'".format(result.stdout.strip("\n")))

        self.logger.info("Checking ninja command...")
        result = self._run_subprocess([self.ninja_command, "--version"],
                                      stdout=subprocess.PIPE, universal_newlines=True)
        if not result.returncode is 0:
            raise BuilderException("Ninja command returned non-zero exit code {}".format(
                result.returncode))
        self.logger.debug("Using ninja version '{!s}'".format(result.stdout.strip("\n")))

    def setup_chromium_source(self):
        '''
        Sets up the Chromium source code in the build sandbox.
        '''
        source_archive = (self._downloads_dir /
                          pathlib.Path("chromium-{version}.tar.xz".format(
                              version=self.chromium_version)))
        source_archive_hashes = (self._downloads_dir /
                                 pathlib.Path("chromium-{version}.tar.xz.hashes".format(
                                     version=self.chromium_version)))

        _util.download_if_needed(self.logger, source_archive,
                                 ("https://commondatastorage.googleapis.com/"
                                  "chromium-browser-official/chromium-{version}.tar.xz").format(
                                      version=self.chromium_version), self.force_download)
        _util.download_if_needed(self.logger, source_archive_hashes,
                                 ("https://commondatastorage.googleapis.com/"
                                  "chromium-browser-official/"
                                  "chromium-{version}.tar.xz.hashes").format(
                                      version=self.chromium_version), self.force_download)

        self.logger.info("Checking source archive integrity...")
        with source_archive_hashes.open("r") as hashes_file:
            for hash_line in hashes_file.read().split("\n"):
                hash_line = hash_line.split("  ")
                if hash_line[0] in hashlib.algorithms_available:
                    self.logger.debug("Running '{}' hash check...".format(hash_line[0]))
                    hasher = hashlib.new(hash_line[0])
                    with source_archive.open("rb") as file_obj:
                        hasher.update(file_obj.read())
                        if not hasher.hexdigest() == hash_line[1]:
                            raise BuilderException(("Archive does not have matching '{algorithm}'"
                                                    "hash '{hashhex}'").format(
                                                        algorithm=hash_line[0],
                                                        hashhex=hash_line[1]))
                else:
                    self.logger.warning("Hash algorithm '{}' not available. Skipping...".format(
                        hash_line[0]))

        self.logger.info("Extracting source archive into building sandbox...")
        if self.run_source_cleaner:
            list_obj = self._read_list_resource(CLEANING_LIST)
            _util.extract_tar_file(self.logger, source_archive, self._sandbox_dir, list_obj,
                                   "chromium-{}".format(self.chromium_version))
            for i in list_obj:
                self.logger.warning("File does not exist in tar file: {}".format(i))
        else:
            _util.extract_tar_file(self.logger, source_archive, self._sandbox_dir, list(),
                                   "chromium-{}".format(self.chromium_version))

        # https://groups.google.com/a/chromium.org/d/topic/chromium-packagers/9JX1N2nf4PU/discussion
        (self._sandbox_dir / pathlib.Path("chrome", "test", "data", "webui",
                                          "i18n_process_css_test.html")).touch()

        extra_deps_dict = self._read_ini_resource(EXTRA_DEPS)
        for section in extra_deps_dict:
            self.logger.info("Downloading extra dependency '{}' ...".format(section))
            dep_version = extra_deps_dict[section]["version"]
            dep_url = extra_deps_dict[section]["url"].format(version=dep_version)
            dep_download_name = extra_deps_dict[section]["download_name"].format(
                version=dep_version)
            if "strip_leading_dirs" in extra_deps_dict[section]:
                dep_strip_dirs = pathlib.Path(
                    extra_deps_dict[section]["strip_leading_dirs"].format(version=dep_version))
            else:
                dep_strip_dirs = None
            self._setup_tar_dependency(dep_url, dep_download_name, dep_strip_dirs,
                                       pathlib.Path(section))

    def setup_build_sandbox(self):
        '''
        Sets up the build sandbox. For now, this function does domain substitution.
        '''
        if self.run_domain_substitution:
            self.logger.info("Running domain substitution over build sandbox...")
            def file_list_generator():
                '''Generator for files in domain substitution list'''

                for list_item in self._read_list_resource(DOMAIN_SUBSTITUTION_LIST):
                    yield self._sandbox_dir / pathlib.Path(list_item)
            _util.domain_substitute(self.logger, self._get_parsed_domain_regexes(),
                                    file_list_generator())

    def apply_patches(self):
        '''Applies patches'''
        # TODO: Use Python to apply patches defined in `patch_order`
        pass

    def setup_build_utilities(self):
        '''Sets up additional build utilities not provided by the build environment'''
        pass

    def generate_build_configuration(self):
        '''Generates build configuration'''
        pass

    def build(self):
        '''Starts building'''
        self.logger.info("Running build command...")
        self._run_ninja(self.build_output, self.build_targets)

    def generate_package(self):
        '''Generates binary packages ready for distribution'''
        # TODO: Create .tar.xz of binaries?
        pass

class QuiltPatchComponent(Builder):
    '''Patching component implemented with quilt'''

    quilt_command = "quilt"

    def __init__(self, *args, **kwargs):
        super(QuiltPatchComponent, self).__init__(*args, **kwargs)

        self.quilt_env_vars = {
            "QUILT_PATCHES": str(pathlib.Path("..") / PATCHES),
            "QUILT_SERIES": str(PATCH_ORDER)
        }

    def apply_patches(self):
        self.logger.debug("Copying patches to {}...".format(str(self.build_dir / PATCHES)))

        if (self.build_dir / PATCHES).exists():
            self.logger.warning("Sandbox patches directory already exists. Trying to unapply...")
            result = self._run_subprocess([self.quilt_command, "pop", "-a"],
                                          append_environ=self.quilt_env_vars,
                                          cwd=str(self._sandbox_dir))
            if not result.returncode == 0 and not result.returncode == 2:
                raise BuilderException("Quilt returned non-zero exit code: {}".format(
                    result.returncode))
            shutil.rmtree(str(self.build_dir / PATCHES))

        self._generate_patches()

        self.logger.info("Applying patches via quilt...")
        result = self._run_subprocess([self.quilt_command, "push", "-a"],
                                      append_environ=self.quilt_env_vars,
                                      cwd=str(self._sandbox_dir))
        if not result.returncode == 0:
            raise BuilderException("Quilt returned non-zero exit code: {}".format(
                result.returncode))

    def check_build_environment(self):
        super(QuiltPatchComponent, self).check_build_environment()

        self.logger.info("Checking quilt command...")
        result = self._run_subprocess([self.quilt_command, "--version"], stdout=subprocess.PIPE,
                                      universal_newlines=True)
        if not result.returncode is 0:
            raise BuilderException("quilt command returned non-zero exit code {}".format(
                result.returncode))
        self.logger.debug("Using quilt command '{!s}'".format(result.stdout.strip("\n")))

class GNUPatchComponent(Builder):
    '''Patching component implemented with GNU patch'''

    patch_command = ["patch", "-p1"]

    def apply_patches(self):
        self.logger.info("Applying patches via '{}' ...".format(" ".join(self.patch_command)))
        self._generate_patches()
        with (self.build_dir / PATCHES / PATCH_ORDER).open() as patch_order_file:
            for i in [x for x in patch_order_file.read().splitlines() if len(x) > 0]:
                self.logger.debug("Applying patch {} ...".format(i))
                with (self.build_dir / PATCHES / i).open("rb") as patch_file:
                    result = self._run_subprocess(self.patch_command, cwd=str(self._sandbox_dir),
                                                  stdin=patch_file)
                    if not result.returncode == 0:
                        raise BuilderException("'{}' returned non-zero exit code {}".format(
                            " ".join(self.patch_command), result.returncode))

    def check_build_environment(self):
        super(GNUPatchComponent, self).check_build_environment()

        self.logger.info("Checking patch command...")
        result = self._run_subprocess([self.patch_command[0], "--version"], stdout=subprocess.PIPE,
                                      universal_newlines=True)
        if not result.returncode is 0:
            raise BuilderException("patch command returned non-zero exit code {}".format(
                result.returncode))
        self.logger.debug("Using patch command '{!s}'".format(result.stdout.split("\n")[0]))

class GYPMetaBuildComponent(Builder):
    '''Meta-build configuration component implemented with GYP'''

    def _get_gyp_flags(self):
        args_dict = dict()
        for i in self._read_list_resource(GYP_FLAGS):
            arg_key, arg_value = i.split("=", 1)
            args_dict[arg_key] = arg_value
        if not self.target_arch is None:
            if self.target_arch == CPUArch.x86:
                args_dict["target_arch"] = "ia32"
            else:
                args_dict["target_arch"] = self.target_arch.value
        return args_dict

    def _gyp_generate_ninja(self, args_dict, append_environ):
        command_list = list()
        if not self.python2_command is None:
            command_list.append(self.python2_command)
        command_list.append(str(pathlib.Path("build", "gyp_chromium")))
        command_list += ["--depth=.", "--check"]
        for arg_key, arg_value in args_dict.items():
            command_list.append("-D{}={}".format(arg_key, arg_value))
        self.logger.debug("GYP command: {}".format(" ".join(command_list)))
        result = self._run_subprocess(command_list, append_environ=append_environ,
                                      cwd=str(self._sandbox_dir))
        if not result.returncode == 0:
            raise BuilderException("GYP command returned non-zero exit code: {}".format(
                result.returncode))

    def generate_build_configuration(self):
        '''Generates build configuration using GYP'''
        self.logger.info("Running gyp command...")
        self._gyp_generate_ninja(self._get_gyp_flags(), None)

class GNMetaBuildComponent(Builder):
    '''Meta-build configuration component implemented with GN'''

    gn_command = None

    def _gn_write_args(self, args_map):
        '''
        `args_map` can be any object supporting the mapping interface
        '''
        gn_imports = list()
        gn_flags = list()
        for gn_path in args_map:
            # Checking against DEFAULT for configparser mapping interface
            if not gn_path == "DEFAULT" and not gn_path == "global":
                if not gn_path.lower().endswith(".gn"):
                    gn_imports.append('import("{}")'.format(gn_path))
            for flag in args_map[gn_path]:
                gn_flags.append("{}={}".format(flag, args_map[gn_path][flag]))
        with (self._sandbox_dir / self.build_output /
              pathlib.Path("args.gn")).open("w") as file_obj:
            file_obj.write("\n".join(gn_imports))
            file_obj.write("\n")
            file_obj.write("\n".join(gn_flags))

    #def _gn_generate_ninja(self, gn_override=None):
    #    command_list = list()
    #    if gn_override is None:
    #        command_list.append(self.gn_command)
    #    else:
    #        command_list.append(gn_override)
    #    command_list.append("gen")
    #    command_list.append(str(self.build_output))
    #    result = self._run_subprocess(command_list, cwd=str(self._sandbox_dir))
    #    if not result.returncode == 0:
    #        raise BuilderException("gn gen returned non-zero exit code: {}".format(
    #            result.returncode))

    #def _build_gn(self):
    #    '''
    #    Build the GN tool to out/gn_tool in the build sandbox. Returns the gn command string.
    #
    #    Only works on Linux or Mac.
    #    '''
    #    self.logger.info("Building gn...")
    #    temp_gn_executable = pathlib.Path("out", "temp_gn")
    #    if (self._sandbox_dir / temp_gn_executable).exists():
    #        self.logger.info("Bootstrap gn already exists")
    #    else:
    #        self.logger.info("Building bootstrap gn")
    #        command_list = [str(pathlib.Path("tools", "gn", "bootstrap", "bootstrap.py")),
    #                        "-v", "-s", "-o", str(temp_gn_executable),
    #                        "--gn-gen-args= use_sysroot=false"]
    #        if not self.python2_command is None:
    #            command_list.insert(0, self.python2_command)
    #        result = self._run_subprocess(command_list, cwd=str(self._sandbox_dir))
    #        if not result.returncode == 0:
    #            raise BuilderException("GN bootstrap command returned "
    #                                   "non-zero exit code: {}".format(result.returncode))
    #    self.logger.info("Building gn using bootstrap gn...")
    #    build_output = pathlib.Path("out", "gn_release")
    #    (self._sandbox_dir / build_output).mkdir(parents=True, exist_ok=True)
    #    self._gn_write_args({"global": {"use_sysroot": "false", "is_debug": "false"}},
    #                        build_output)
    #    self._gn_generate_ninja(build_output, gn_override=str(temp_gn_executable))
    #    self._run_ninja(build_output, ["gn"])
    #    return str(build_output / pathlib.Path("gn"))

    #def setup_build_utilities(self, build_gn=True, gn_command=None, python2_command=None,
    #                          ninja_command="ninja"):
    #    '''
    #    Sets up the utilities required for building. For now, this is just the "gn" tool.
    #
    #    If `build_gn` is True, then the `tools/gn/bootstrap/bootstrap.py` script is invoked
    #    in the build directory to build gn.
    #    If `python2_command` is set, it must be a string of a command to invoke Python 2 for
    #    running bootstrap.py. Otherwise, the bootstrap.py path will be the executable path.
    #
    #    If `gn_command` is set, it must be a string of a command to invoke gn.
    #
    #    `build_gn` and `gn_command` are mutually exclusive.
    #    '''
    #    if build_gn and not gn_command is None:
    #        raise BuilderException("Conflicting arguments: build_gn and gn_path")
    #    self.ninja_command = ninja_command
    #    if build_gn:
    #        self.gn_command = self._build_gn(python2_command)
    #    else:
    #        self.gn_command = gn_command

    #def generate_build_configuration(self, gn_args=pathlib.Path("gn_args.ini"),
    #                                 build_output=pathlib.Path("out", "Default")):
    #    (self._sandbox_dir / build_output).mkdir(parents=True, exist_ok=True)
    #    config = configparser.ConfigParser()
    #    config.read(str(gn_args))
    #    self._gn_write_args(config, build_output)
    #    self._gn_generate_ninja(build_output)
