#!/usr/bin/env python
#
#    weewx --- A simple, high-performance weather station server
#
#    Copyright (c) 2009-2015 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

from __future__ import with_statement

import os.path
import sys
import re
import tempfile
import shutil

import configobj

from distutils.core import setup
from distutils.command.install import install
from distutils.command.install_data import install_data
from distutils.command.install_lib import install_lib
from distutils.command.install_scripts import install_scripts
from distutils.command.sdist import sdist

# Useful for debugging setup.py. Set the environment variable
# DISTUTILS_DEBUG to get more info.
from distutils.debug import DEBUG

# Find the install bin subdirectory:
this_file = os.path.join(os.getcwd(), __file__)
this_dir = os.path.abspath(os.path.dirname(this_file))
bin_dir = os.path.abspath(os.path.join(this_dir, 'bin'))

# Now that we've found the bin subdirectory, inject it into the path:
sys.path.insert(0, bin_dir)

# Now we can import some weewx modules
import weewx
VERSION = weewx.__version__
import config_util
import weeutil.weeutil

# The default station information:
stn_info = {'location'     : '',
            'latitude'     : '0',
            'longitude'    : '0',
            'altitude'     : ['0', 'meter'],
            'units'        : 'metric',
            'station_type' : 'Simulator',
            'driver'       : 'weewx.drivers.Simulator'}

#==============================================================================
# install
#==============================================================================

class weewx_install(install):
    """Specialized version of install. It adds a --no-prompt option, and, if not
    set, prompts for information on a new install."""
    # Add an option for --no-prompt:
    user_options = install.user_options + [('no-prompt', None, 'Do not prompt for info')]

    def run(self, *args, **kwargs):
        global stn_info
        
        config_path = os.path.join(self.home, 'weewx.conf')
        # Is there an old config file? 
        if not os.path.isfile(config_path):
            # No old config file. Must be a new install. If we can prompt,
            # then get the station info from the user. Otherwise, just
            # use the defaults
            if not self.no_prompt:
                # Prompt the user for the station information:
                stn_info = config_util.prompt_for_info()
                driver = config_util.prompt_for_driver(stn_info.get('driver'))
                stn_info['driver'] = driver
                stn_info.update(config_util.prompt_for_driver_settings(driver))

        if DEBUG:
            print "Station info =", stn_info
            
        install.run(self, *args, **kwargs)

    def initialize_options(self, *args, **kwargs):
        self.no_prompt = False
        install.initialize_options(self, *args, **kwargs)

#==============================================================================
# install_lib
#==============================================================================

class weewx_install_lib(install_lib):
    """Specialized version of install_lib""" 

    def run(self):
        # Determine whether the user is still using an old-style schema
        schema_type = get_schema_type(self.install_dir)

        # Save any existing 'bin' subdirectory:
        if os.path.exists(self.install_dir):
            bin_savedir = weeutil.weeutil.save_with_timestamp(self.install_dir)
            print "Saved bin subdirectory as %s" % bin_savedir
        else:
            bin_savedir = None

        # Run the superclass's version. This will install all incoming files.
        install_lib.run(self)
        
        # If the bin subdirectory previously existed, and if it included
        # a 'user' subsubdirectory, then restore it
        if bin_savedir:
            user_backupdir = os.path.join(bin_savedir, 'user')
            if os.path.exists(user_backupdir):
                user_dir = os.path.join(self.install_dir, 'user')
                distutils.dir_util.copy_tree(user_backupdir, user_dir)

        # But, there is one exception: if the old user subdirectory included an
        # old-style schema, then it should be overwritten with the new version.
        if schema_type == 'old':
            incoming_schema_path = os.path.join(bin_dir, 'user/schemas.py')
            target_path = os.path.join(self.install_dir, 'user/schemas.py')
            distutils.file_util.copy_file(incoming_schema_path, target_path)

#==============================================================================
# install_data
#==============================================================================

class weewx_install_data(install_data):
    """Specialized version of install_data """
    
    def copy_file(self, f, install_dir, **kwargs):
        # If this is the configuration file, then merge it instead
        # of copying it
        if f == 'weewx.conf':
            rv = self.process_config_file(f, install_dir, **kwargs)
        elif f in start_scripts:
            rv = self.massage_start_file(f, install_dir, **kwargs)
        else:
            rv = install_data.copy_file(self, f, install_dir, **kwargs)
        return rv
    
    def run(self):
        # If there is an existing skins subdirectory, do not overwrite it.
        if os.path.exists(os.path.join(self.install_dir, 'skins')):
            # Do this by filtering it out of the list of subdirectories to
            # be installed:
            self.data_files = filter(lambda dat : not dat[0].startswith('skins/'), self.data_files)

        remove_obsolete_files(self.install_dir)
        
        # Run the superclass's run():
        install_data.run(self)
       
    def process_config_file(self, f, install_dir, **kwargs):

        # The path where the weewx.conf configuration file will be installed
        install_path = os.path.join(install_dir, os.path.basename(f))
        
        # Open up and parse the distribution config file:
        try:        
            dist_config_dict = configobj.ConfigObj(f, file_error=True)
        except IOError, e:
            sys.exit(str(e))
        except SyntaxError, e:
            sys.exit("Syntax error in distribution configuration file '%s': %s" % 
                     (f, e))
        
        # Do we have an old config file?
        if os.path.isfile(install_path):
            # Yes. Read it
            config_path, config_dict = config_util.read_config(install_path, None)
            if DEBUG:
                print "Old configuration file found at", config_path

            # Update the old configuration file:
            config_util.update_config(config_dict)
            
            # Then merge it into the distribution file
            config_util.merge_config(config_dict, dist_config_dict)
        else:        
            config_dict = dist_config_dict
            config_util.modify_config(config_dict, stn_info, DEBUG)
    
        # Get a temporary file:
        with tempfile.NamedTemporaryFile("w") as tmpfile:
            # Write the finished configuration file to it:
            config_dict.write(tmpfile)
            tmpfile.flush()
            
            # Save the old config file if it exists:
            if os.path.exists(install_path):
                backup_path = save_path(install_path)
                print "Saved old configuration file as %s" % backup_path
                
            # Now install the temporary file (holding the merged config data)
            # into the proper place:
            rv = install_data.copy_file(self, tmpfile.name, install_path, **kwargs)
        
        # Set the permission bits unless this is a dry run:
        if not self.dry_run:
            shutil.copymode(f, install_path)

        return rv

def massage_start_file(self, f, install_dir, **kwargs):

        outname = os.path.join(install_dir, os.path.basename(f))
        sre = re.compile(r"WEEWX_ROOT\s*=")

        with open(f, 'r') as infile:
            with tempfile.NamedTemporaryFile("w") as tmpfile:
                for line in infile:
                    if sre.match(line):
                        tmpfile.writelines("WEEWX_ROOT=%s\n" % self.install_dir)
                    else:
                        tmpfile.writelines(line)
                tmpfile.flush()
                rv = install_data.copy_file(self, tmpfile.name, outname, **kwargs)

        # Set the permission bits unless this is a dry run:
        if not self.dry_run:
            shutil.copymode(f, outname)

        return rv

#==============================================================================
# install_scripts
#==============================================================================

class weewx_install_scripts(install_scripts):

    def run(self):
        # Run the superclass's version:
        install_scripts.run(self)

        try:
            # Put in a symbolic link for weewxd.py
            os.symlink('./weewxd', os.path.join(self.install_dir, 'weewxd.py'))
        except OSError:
            pass

#==============================================================================
# sdist
#==============================================================================

class weewx_sdist(sdist):
    """Specialized version of sdist which checks for password information in
    the configuration file before creating the distribution.

    For other sdist methods, see:
 http://epydoc.sourceforge.net/stdlib/distutils.command.sdist.sdist-class.html
    """

    def copy_file(self, f, install_dir, **kwargs):
        """Specialized version of copy_file that checks for stray passwords."""

        # If this is the configuration file, then check it for passwords
        if f == 'weewx.conf':
            import configobj
            config = configobj.ConfigObj(f)

            try:
                password = config['StdReport']['FTP']['password']
                sys.exit("\n*** FTP password found in configuration file. Aborting ***\n\n")
            except KeyError:
                pass

            try:
                password = config['StdRESTful']['Wunderground']['password']
                sys.exit("\n*** Wunderground password found in configuration file. Aborting ***\n\n")
            except KeyError:
                pass

            try:
                password = config['StdRESTful']['Wunderground']['password']
                sys.exit("\n*** PWSweather password found in configuration file. Aborting ***\n\n")
            except KeyError:
                pass

        # Pass on to my superclass:
        return sdist.copy_file(self, f, install_dir, **kwargs)

#==============================================================================
# utility functions
#==============================================================================

def remove_obsolete_files(install_dir):
    """Remove no longer needed files from the installation
    directory, nominally /home/weewx."""
    
    # If the file #upstream.last exists, delete it, as it is no longer used.
    try:
        os.remove(os.path.join(install_dir, 'public_html/#upstream.last'))
    except OSError:
        pass
        
    # If the file $WEEWX_INSTALL/readme.htm exists, delete it. It's
    # the old readme (since replaced with README)
    try:
        os.remove(os.path.join(install_dir, 'readme.htm'))
    except OSError:
        pass
    
    # If the file $WEEWX_INSTALL/CHANGES.txt exists, delete it. It's
    # been moved to the docs subdirectory and renamed
    try:
        os.remove(os.path.join(install_dir, 'CHANGES.txt'))
    except OSError:
        pass
    
    # The directory start_scripts is no longer used
    shutil.rmtree(os.path.join(install_dir, 'start_scripts'), True)
    
    # The file docs/README.txt is now gone
    try:
        os.remove(os.path.join(install_dir, 'docs/README.txt'))
    except OSError:
        pass
    
    # If the file docs/CHANGES.txt exists, delete it. It's been renamed
    # to docs/changes.txt
    try:
        os.remove(os.path.join(install_dir, 'docs/CHANGES.txt'))
    except OSError:
        pass

def get_schema_type(bin_dir):
    """Checks whether the schema in user.schemas is a new style or old style
    schema.
    
    bin_dir: The directory to be checked. This is nominally /home/weewx/bin.
    
    Returns:
      'none': There is no schema at all.
      'old' : It is an old-style schema.
      'new' : It is a new-style schema
    """
    tmp_path = list(sys.path)
    sys.path.insert(0, bin_dir)
    
    try:
        import user.schemas
    except ImportError:
        # There is no existing schema at all.
        result = 'none'
    else:
        # There is a schema. Determine if it is old-style or new-style
        try:
            # Try the old style 'drop_list'. If it fails, it must be
            # a new-style schema
            _ = user.schemas.drop_list # @UnusedVariable @UndefinedVariable
        except AttributeError:
            # New style schema 
            result = 'new'
        else:
            # It did not fail. Must be an old-style schema
            result = 'old'
        finally:
            del user.schemas

    # Restore the path        
    sys.path = tmp_path
    
    return result

#==============================================================================
# main entry point
#==============================================================================

if __name__ == "__main__":
    # Get the data to be installed from the manifest:

    # now invoke the standard python setup
    setup(name='weewx',
          version=VERSION,
          description='weather software',
          long_description="weewx interacts with a weather station to produce graphs, "
          "reports, and HTML pages.  weewx can upload data to services such as the "
          "WeatherUnderground, PWSweather.com, or CWOP.",
          author='Tom Keffer',
          author_email='tkeffer@gmail.com',
          url='http://www.weewx.com',
          license='GPLv3',
          classifiers=['Development Status :: 5 - Production/Stable',
                       'Intended Audience :: End Users/Desktop',
                       'License :: GPLv3',
                       'Operating System :: OS Independent',
                       'Programming Language :: Python',
                       'Programming Language :: Python :: 2',
                       ],
          requires=['configobj(>=4.5)',
                    'serial(>=2.3)',
                    'Cheetah(>=2.0)',
                    'sqlite3(>=2.5)',
                    'PIL(>=1.1.6)'],
          provides=['weedb',
                    'weeplot',
                    'weeutil',
                    'weewx'],
          cmdclass={"sdist"   : weewx_sdist,
                    "install" : weewx_install,
                    "install_scripts": weewx_install_scripts,
                    "install_data"   : weewx_install_data,
                    "install_lib"    : weewx_install_lib},
          platforms=['any'],
          package_dir={'': 'bin'},
          packages=['examples',
                    'schemas',
                    'user',
                    'weedb',
                    'weeplot',
                    'weeutil',
                    'weewx',
                    'weewx.drivers'],
          py_modules=['daemon',
                      'config_util'],
          scripts=['bin/wee_config_database',
                   'bin/wee_config_device',
                   'bin/weewxd',
                   'bin/wee_reports'],
          data_files=[
                      ('',
                       ['LICENSE.txt',
                        'README',
                        'weewx.conf']),
                      ('docs',
                       ['docs/changes.txt',
                        'docs/copyright.htm',
                        'docs/customizing.htm',
                        'docs/debian.htm',
                        'docs/readme.htm',
                        'docs/redhat.htm',
                        'docs/setup.htm',
                        'docs/suse.htm',
                        'docs/upgrading.htm',
                        'docs/usersguide.htm']),
                      ('docs/css',
                       ['docs/css/jquery.tocify.css',
                        'docs/css/weewx_docs.css']),
                      ('docs/css/ui-lightness',
                       ['docs/css/ui-lightness/jquery-ui-1.10.4.custom.css',
                        'docs/css/ui-lightness/jquery-ui-1.10.4.custom.min.css']),
                      ('docs/css/ui-lightness/images',
                       ['docs/css/ui-lightness/images/animated-overlay.gif',
                        'docs/css/ui-lightness/images/ui-bg_diagonals-thick_18_b81900_40x40.png',
                        'docs/css/ui-lightness/images/ui-bg_diagonals-thick_20_666666_40x40.png',
                        'docs/css/ui-lightness/images/ui-bg_flat_10_000000_40x100.png',
                        'docs/css/ui-lightness/images/ui-bg_glass_100_f6f6f6_1x400.png',
                        'docs/css/ui-lightness/images/ui-bg_glass_100_fdf5ce_1x400.png',
                        'docs/css/ui-lightness/images/ui-bg_glass_65_ffffff_1x400.png',
                        'docs/css/ui-lightness/images/ui-bg_gloss-wave_35_f6a828_500x100.png',
                        'docs/css/ui-lightness/images/ui-bg_highlight-soft_100_eeeeee_1x100.png',
                        'docs/css/ui-lightness/images/ui-bg_highlight-soft_75_ffe45c_1x100.png',
                        'docs/css/ui-lightness/images/ui-icons_222222_256x240.png',
                        'docs/css/ui-lightness/images/ui-icons_228ef1_256x240.png',
                        'docs/css/ui-lightness/images/ui-icons_ef8c08_256x240.png',
                        'docs/css/ui-lightness/images/ui-icons_ffd27a_256x240.png',
                        'docs/css/ui-lightness/images/ui-icons_ffffff_256x240.png']),
                      ('docs/images',
                       ['docs/images/day-gap-not-shown.png',
                        'docs/images/day-gap-showing.png',
                        'docs/images/daycompare.png',
                        'docs/images/daytemp_with_avg.png',
                        'docs/images/daywindvec.png',
                        'docs/images/ferrites.jpg',
                        'docs/images/funky_degree.png',
                        'docs/images/image_parts.png',
                        'docs/images/image_parts.xcf',
                        'docs/images/logo-apple.png',
                        'docs/images/logo-centos.png',
                        'docs/images/logo-debian.png',
                        'docs/images/logo-fedora.png',
                        'docs/images/logo-linux.png',
                        'docs/images/logo-mint.png',
                        'docs/images/logo-opensuse.png',
                        'docs/images/logo-redhat.png',
                        'docs/images/logo-suse.png',
                        'docs/images/logo-ubuntu.png',
                        'docs/images/logo-weewx.png',
                        'docs/images/sample_monthrain.png',
                        'docs/images/weekgustoverlay.png',
                        'docs/images/weektempdew.png',
                        'docs/images/yearhilow.png']),
                      ('docs/js',
                       ['docs/js/jquery-1.11.1.min.js',
                        'docs/js/jquery-ui-1.10.4.custom.min.js',
                        'docs/js/jquery.toc-1.1.4.min.js',
                        'docs/js/jquery.tocify-1.9.0.js',
                        'docs/js/jquery.tocify-1.9.0.min.js',
                        'docs/js/weewx.js']),
                      ('skins/Ftp',
                       ['skins/Ftp/skin.conf']),
                      ('skins/Rsync',
                       ['skins/Rsync/skin.conf']),
                      ('skins/Standard',
                       ['skins/Standard/favicon.ico',
                        'skins/Standard/index.html.tmpl',
                        'skins/Standard/mobile.css',
                        'skins/Standard/mobile.html.tmpl',
                        'skins/Standard/month.html.tmpl',
                        'skins/Standard/skin.conf',
                        'skins/Standard/week.html.tmpl',
                        'skins/Standard/weewx.css',
                        'skins/Standard/year.html.tmpl']),
                      ('skins/Standard/NOAA',
                       ['skins/Standard/NOAA/NOAA-YYYY-MM.txt.tmpl',
                        'skins/Standard/NOAA/NOAA-YYYY.txt.tmpl']),
                      ('skins/Standard/RSS',
                       ['skins/Standard/RSS/weewx_rss.xml.tmpl']),
                      ('skins/Standard/backgrounds',
                       ['skins/Standard/backgrounds/band.gif',
                        'skins/Standard/backgrounds/butterfly.jpg',
                        'skins/Standard/backgrounds/drops.gif',
                        'skins/Standard/backgrounds/flower.jpg',
                        'skins/Standard/backgrounds/leaf.jpg',
                        'skins/Standard/backgrounds/night.gif']),
                      ('skins/Standard/smartphone',
                       ['skins/Standard/smartphone/barometer.html.tmpl',
                        'skins/Standard/smartphone/custom.js',
                        'skins/Standard/smartphone/humidity.html.tmpl',
                        'skins/Standard/smartphone/index.html.tmpl',
                        'skins/Standard/smartphone/radar.html.tmpl',
                        'skins/Standard/smartphone/rain.html.tmpl',
                        'skins/Standard/smartphone/temp_outside.html.tmpl',
                        'skins/Standard/smartphone/wind.html.tmpl']),
                      ('skins/Standard/smartphone/icons',
                       ['skins/Standard/smartphone/icons/icon_ipad_x1.png',
                        'skins/Standard/smartphone/icons/icon_ipad_x2.png',
                        'skins/Standard/smartphone/icons/icon_iphone_x1.png',
                        'skins/Standard/smartphone/icons/icon_iphone_x2.png']),
                      ('util/apache/conf.d',
                       ['util/apache/conf.d/weewx.conf']),
                      ('util/init.d',
                       ['util/init.d/weewx.bsd',
                        'util/init.d/weewx.debian',
                        'util/init.d/weewx.lsb',
                        'util/init.d/weewx.redhat',
                        'util/init.d/weewx.suse']),
                      ('util/launchd',
                       ['util/launchd/com.weewx.weewxd.plist']),
                      ('util/logrotate.d',
                       ['util/logrotate.d/weewx']),
                      ('util/logwatch/conf/logfiles',
                       ['util/logwatch/conf/logfiles/weewx.conf']),
                      ('util/logwatch/conf/services',
                       ['util/logwatch/conf/services/weewx.conf']),
                      ('util/logwatch/scripts/services',
                       ['util/logwatch/scripts/services/weewx']),
                      ('util/rsyslog.d',
                       ['util/rsyslog.d/weewx.conf']),
                      ('util/systemd',
                       ['util/systemd/weewx.service'])
                      ]
          )
