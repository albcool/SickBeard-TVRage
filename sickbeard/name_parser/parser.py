# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of Sick Beard.
#
# Sick Beard is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sick Beard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Sick Beard.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import os.path
import re
import copy
import regexes
import sickbeard
import calendar

from sickbeard import logger, classes
from sickbeard import scene_numbering, scene_exceptions
from sickbeard.indexers import indexer_api, indexer_exceptions
from sickbeard.common import indexerStrings

from lib.dateutil.parser import parse

from time import strptime

class NameParser(object):
    def __init__(self, file_name=True):

        self.file_name = file_name
        self.compiled_regexes = []
        self._compile_regexes()

    def clean_series_name(self, series_name):
        """Cleans up series name by removing any . and _
        characters, along with any trailing hyphens.
    
        Is basically equivalent to replacing all _ and . with a
        space, but handles decimal numbers in string, for example:
    
        >>> cleanRegexedSeriesName("an.example.1.0.test")
        'an example 1.0 test'
        >>> cleanRegexedSeriesName("an_example_1.0_test")
        'an example 1.0 test'
        
        Stolen from dbr's tvnamer
        """
        
        series_name = re.sub("(\D)\.(?!\s)(\D)", "\\1 \\2", series_name)
        series_name = re.sub("(\d)\.(\d{4})", "\\1 \\2", series_name) # if it ends in a year then don't keep the dot
        series_name = re.sub("(\D)\.(?!\s)", "\\1 ", series_name)
        series_name = re.sub("\.(?!\s)(\D)", " \\1", series_name)
        series_name = series_name.replace("_", " ")
        series_name = re.sub("-$", "", series_name)
        series_name = re.sub("^\[.*\]", "",  series_name)
        return series_name.strip()

    def _compile_regexes(self):
        for (cur_pattern_name, cur_pattern) in regexes.ep_regexes:
            try:
                cur_regex = re.compile(cur_pattern, re.VERBOSE | re.IGNORECASE)
            except re.error, errormsg:
                logger.log(u"WARNING: Invalid episode_pattern, %s. %s" % (errormsg, cur_pattern))
            else:
                self.compiled_regexes.append((cur_pattern_name, cur_regex))

    def _parse_string(self, name):
        
        if not name:
            return None
        
        for (cur_regex_name, cur_regex) in self.compiled_regexes:
            match = cur_regex.match(name)

            if not match:
                continue
            
            result = ParseResult(name)
            result.which_regex = [cur_regex_name]
            
            named_groups = match.groupdict().keys()

            if 'series_name' in named_groups:
                result.series_name = match.group('series_name')
                if result.series_name:
                    result.series_name = self.clean_series_name(result.series_name)
            
            if 'season_num' in named_groups:
                tmp_season = int(match.group('season_num'))
                if cur_regex_name == 'bare' and tmp_season in (19,20):
                    continue
                result.season_number = tmp_season
            
            if 'ep_num' in named_groups:
                ep_num = self._convert_number(match.group('ep_num'))
                if 'extra_ep_num' in named_groups and match.group('extra_ep_num'):
                    result.episode_numbers = range(ep_num, self._convert_number(match.group('extra_ep_num'))+1)
                else:
                    result.episode_numbers = [ep_num]

            if 'air_year' in named_groups and 'air_month' in named_groups and 'air_day' in named_groups:
                if 'scene_sports_date_format' in cur_regex_name:
                    year = match.group('air_year')
                    month = strptime(match.group('air_month')[:3],'%b').tm_mon
                    day = re.sub("(st|nd|rd|th)", "", match.group('air_day'))
                else:
                    year = int(match.group('air_year'))
                    month = int(match.group('air_month'))
                    day = int(match.group('air_day'))

                try:
                    dtStr = '%s-%s-%s' % (year, month, day)
                    result.air_date = datetime.datetime.strptime(dtStr, "%Y-%m-%d").date()
                except ValueError, e:
                    raise InvalidNameException(e.message)

            if 'extra_info' in named_groups:
                tmp_extra_info = match.group('extra_info')
                
                # Show.S04.Special is almost certainly not every episode in the season
                if tmp_extra_info and cur_regex_name == 'season_only' and re.match(r'([. _-]|^)(special|extra)\w*([. _-]|$)', tmp_extra_info, re.I):
                    continue
                result.extra_info = tmp_extra_info
            
            if 'release_group' in named_groups:
                result.release_group = match.group('release_group')

            return result
        
        return None

    def _combine_results(self, first, second, attr):
        # if the first doesn't exist then return the second or nothing
        if not first:
            if not second:
                return None
            else:
                return getattr(second, attr)

        # if the second doesn't exist then return the first
        if not second:
            return getattr(first, attr)
        
        a = getattr(first, attr)
        b = getattr(second, attr)
        
        # if a is good use it
        if a != None or (type(a) == list and len(a)):
            return a
        # if not use b (if b isn't set it'll just be default)
        else:
            return b

    def _unicodify(self, obj, encoding = "utf-8"):
        if isinstance(obj, basestring):
            if not isinstance(obj, unicode):
                obj = unicode(obj, encoding)
        return obj

    def _convert_number(self, number):
        
        try:
            return int(number)
        except:
            numeral_map = zip(
                (1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1),
                ('M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I')
            )
                
            n = unicode(number).upper()    
            
            i = result = 0
            for integer, numeral in numeral_map:
                while n[i:i + len(numeral)] == numeral:
                    result += integer
                    i += len(numeral)
            
            return result

    def parse(self, name, fix_scene_numbering=False):
        name = self._unicodify(name)

        cached = name_parser_cache.get(name)

        if cached:
            if fix_scene_numbering:
                cached_fixed = copy.copy(cached)
                cached_fixed.fix_scene_numbering()
                return cached_fixed
            return cached

        # break it into parts if there are any (dirname, file name, extension)
        dir_name, file_name = os.path.split(name)
        ext_match = re.match('(.*)\.\w{3,4}$', file_name)
        if ext_match and self.file_name:
            base_file_name = ext_match.group(1)
        else:
            base_file_name = file_name

        # use only the direct parent dir
        dir_name = os.path.basename(dir_name)

        # set up a result to use
        final_result = ParseResult(name)

        # try parsing the file name
        file_name_result = self._parse_string(base_file_name)

        # parse the dirname for extra info if needed
        dir_name_result = self._parse_string(dir_name)

        # build the ParseResult object
        final_result.air_date = self._combine_results(file_name_result, dir_name_result, 'air_date')

        if not final_result.air_date:
            final_result.season_number = self._combine_results(file_name_result, dir_name_result, 'season_number')
            final_result.episode_numbers = self._combine_results(file_name_result, dir_name_result, 'episode_numbers')

        # if the dirname has a release group/show name I believe it over the filename
        final_result.series_name = self._combine_results(dir_name_result, file_name_result, 'series_name')
        final_result.extra_info = self._combine_results(dir_name_result, file_name_result, 'extra_info')
        final_result.release_group = self._combine_results(dir_name_result, file_name_result, 'release_group')

        final_result.which_regex = []
        if final_result == file_name_result:
            final_result.which_regex = file_name_result.which_regex
        elif final_result == dir_name_result:
            final_result.which_regex = dir_name_result.which_regex
        else:
            if file_name_result:
                final_result.which_regex += file_name_result.which_regex
            if dir_name_result:
                final_result.which_regex += dir_name_result.which_regex

        # if there's no useful info in it then raise an exception
        if final_result.season_number == None and not final_result.episode_numbers and final_result.air_date == None and not final_result.series_name:
            raise InvalidNameException("Unable to parse " + name.encode(sickbeard.SYS_ENCODING, 'xmlcharrefreplace'))

        name_parser_cache.add(name, final_result)

        if fix_scene_numbering:
            result_fixed = copy.copy(final_result)
            result_fixed.fix_scene_numbering()
            return result_fixed

        return final_result
    
    @classmethod
    def series_name_to_indexer_id(cls, series_name, check_scene_exceptions=True, check_database=True, check_indexer=False):
        """
        Given a series name, return it's tvdbd_id.
        Returns None if not found.
        
        This is mostly robbed from postProcessor._analyze_name
        """
    
        # do a scene reverse-lookup to get a list of all possible names
        name_list = sickbeard.show_name_helpers.sceneToNormalShowNames(series_name)
        
        # for each possible interpretation of that scene name
        if check_scene_exceptions:
            for cur_name in name_list:
                logger.log(u"Checking scene exceptions for a match on "+cur_name, logger.DEBUG)
                scene_id = sickbeard.scene_exceptions.get_scene_exception_by_name(cur_name)
                if scene_id: return scene_id

        # see if we can find the name directly in the DB, if so use it
        if check_database:
            for cur_name in name_list:
                logger.log(u"Looking up "+cur_name+u" in the DB", logger.DEBUG)
                db_result = sickbeard.helpers.searchDBForShow(cur_name)
                if db_result: return db_result[1]
        
        # see if we can find the name with a TVDB lookup
        if check_indexer:
            for cur_name in name_list:
                for indexer in indexerStrings:
                    try:
                        lINDEXER_API_PARMS = {'indexer': indexer}

                        lINDEXER_API_PARMS['custom_ui'] = classes.ShowListUI

                        t = indexer_api.indexerApi(**lINDEXER_API_PARMS)

                        logger.log(u"Looking up name "+cur_name+u" on the Indexer", logger.DEBUG)
                        showObj = t[cur_name]
                    except (indexer_exceptions):
                        # if none found, search on all languages
                        try:
                            lINDEXER_API_PARMS = {'indexer': indexer}

                            lINDEXER_API_PARMS['custom_ui'] = classes.ShowListUI
                            lINDEXER_API_PARMS['search_all_languages'] = True

                            t = indexer_api.indexerApi(**lINDEXER_API_PARMS)

                            logger.log(u"Looking up name "+cur_name+u" in all languages on the Indexer", logger.DEBUG)
                            showObj = t[cur_name]
                        except (indexer_exceptions.indexer_exception, IOError):
                            pass

                        continue
                    except (IOError):
                        continue

                    return showObj["id"]
            
        return None

class ParseResult(object):
    def __init__(self,
                 original_name,
                 series_name=None,
                 season_number=None,
                 episode_numbers=None,
                 extra_info=None,
                 release_group=None,
                 air_date=None
                 ):

        self.original_name = original_name
        
        self.series_name = series_name
        self.season_number = season_number
        if not episode_numbers:
            self.episode_numbers = []
        else:
            self.episode_numbers = episode_numbers

        self.extra_info = extra_info
        self.release_group = release_group
        
        self.air_date = air_date
        
        self.which_regex = None
        
    def __eq__(self, other):
        if not other:
            return False
        
        if self.series_name != other.series_name:
            return False
        if self.season_number != other.season_number:
            return False
        if self.episode_numbers != other.episode_numbers:
            return False
        if self.extra_info != other.extra_info:
            return False
        if self.release_group != other.release_group:
            return False
        if self.air_date != other.air_date:
            return False
        
        return True

    def __str__(self):
        if self.series_name != None:
            to_return = self.series_name + u' - '
        else:
            to_return = u''
        if self.season_number != None:
            to_return += 'S'+str(self.season_number)
        if self.episode_numbers and len(self.episode_numbers):
            for e in self.episode_numbers:
                to_return += 'E'+str(e)

        if self.air_by_date:
            to_return += str(self.air_date)

        if self.extra_info:
            to_return += ' - ' + self.extra_info
        if self.release_group:
            to_return += ' (' + self.release_group + ')'

        to_return += ' [ABD: '+str(self.air_by_date)+']'

        return to_return.encode('utf-8')

    def _is_air_by_date(self):
        if self.season_number == None and len(self.episode_numbers) == 0 and self.air_date:
            return True
        return False
    air_by_date = property(_is_air_by_date)
    
    def fix_scene_numbering(self):
        """
        The changes the parsed result (which is assumed to be scene numbering) to
        tvdb numbering, if necessary.
        """
        if self.air_by_date: return self # scene numbering does not apply to air-by-date
        if self.season_number == None: return self # can't work without a season
        if len(self.episode_numbers) == 0: return self # need at least one episode
        
        indexer_id = NameParser.series_name_to_indexer_id(self.series_name, True, True, False)
        
        new_episode_numbers = []
        new_season_numbers = []
        for epNo in self.episode_numbers:
            (s, e) = scene_numbering.get_indexer_numbering(indexer_id, self.season_number, epNo)
            new_episode_numbers.append(e)
            new_season_numbers.append(s)
            
        # need to do a quick sanity check here.  It's possible that we now have episodes
        # from more than one season (by tvdb numbering), and this is just too much
        # for sickbeard, so we'd need to flag it.
        new_season_numbers = list(set(new_season_numbers)) # remove duplicates
        if len(new_season_numbers) > 1:
            raise InvalidNameException("Scene numbering results episodes from "
                                       "seasons %s, (i.e. more than one) and "
                                       "sickbeard does not support this.  "
                                       "Sorry." % (str(new_season_numbers)))
            
        # I guess it's possible that we'd have duplicate episodes too, so lets
        # eliminate them
        new_episode_numbers = list(set(new_episode_numbers))
        new_episode_numbers.sort()
        
        self.episode_numbers = new_episode_numbers
        self.season_number = new_season_numbers[0]

        return self

class NameParserCache(object):
    #TODO: check if the fifo list can beskiped and only use one dict
    _previous_parsed_list = [] # keep a fifo list of the cached items
    _previous_parsed = {}
    _cache_size = 100
    
    def add(self, name, parse_result):
        self._previous_parsed[name] = parse_result
        self._previous_parsed_list.append(name)
        while len(self._previous_parsed_list) > self._cache_size:
            del_me = self._previous_parsed_list.pop(0)
            self._previous_parsed.pop(del_me)
    
    def get(self, name):
        if name in self._previous_parsed:
            logger.log("Using cached parse result for: " + name, logger.DEBUG)
            return self._previous_parsed[name]
        else:
            return None

name_parser_cache = NameParserCache()

class InvalidNameException(Exception):
    "The given name is not valid"
