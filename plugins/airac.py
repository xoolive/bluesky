"""A plugin for adding sectors referenced in AIRAC files.

Settings:
    - `airac_path` must be set in the settings.cfg. The directory should contain
    the AIRAC zip files. The package will unpack them in a temporary directory.

Usage:
    AIRAC EHAA          # adds an AREA
    AIRAC LFFF/UIR      # specify the type of sector if there are doublons
    AIRAC LFFF/FIR      # specify the type of sector if there are doublons
    AIRAC LFBBBDX       # more specific compounds sectors

Dependency:
    shapely

TODO:
    Add support for altitudes and compound areas.

Xavier Olive, 2018

"""

import os
import zipfile
import tempfile
from pathlib import Path
from functools import lru_cache
from xml.etree import ElementTree

from bluesky import settings
from bluesky.tools import areafilter

import numpy as np

from shapely.ops import cascaded_union
from shapely.geometry import Polygon

settings.set_variable_defaults(airac_path=None)

full_dict = {}
all_points = {}

tree = None
ns = {'adrmsg': 'http://www.eurocontrol.int/cfmu/b2b/ADRMessage',
      'aixm': 'http://www.aixm.aero/schema/5.1',
      'gml': 'http://www.opengis.net/gml/3.2',
      'xlink': 'http://www.w3.org/1999/xlink'}


def init_plugin():

    config = {
        'plugin_name': 'AIRAC',
        'plugin_type': 'sim',
    }

    stackfunctions = {

        'AIRAC': [
            'AIRAC SECTOR[/TYPE]',
            'txt',
            create_area,
            'Create Polygons from AIRAC data']

    }

    init_airac()

    return config, stackfunctions

def init_airac():
    global tree

    extractdir = tempfile.mkdtemp()

    if settings.airac_path is None:
        msg = 'You must define an airac_path variable in your settings'
        raise Exception(msg)

    airac_path = Path(settings.airac_path)

    assert airac_path.is_dir()

    for filename in ['Airspace.BASELINE', 'DesignatedPoint.BASELINE',
                     'Navaid.BASELINE']:

        zippath = zipfile.ZipFile(airac_path.joinpath(f"{filename}.zip"))
        zippath.extractall(path=extractdir)

    tree = ElementTree.parse(os.path.join(extractdir, 'Airspace.BASELINE'))


    for airspace in tree.findall('adrmsg:hasMember/aixm:Airspace', ns):
        full_dict[airspace.find('gml:identifier', ns).text] = airspace


    points = ElementTree.parse(
        os.path.join(extractdir, 'DesignatedPoint.BASELINE'))

    for point in points.findall("adrmsg:hasMember/aixm:DesignatedPoint", ns):
        all_points[point.find("gml:identifier", ns).text] = tuple(float(x) for x in point.find("aixm:timeSlice/aixm:DesignatedPointTimeSlice/aixm:location/aixm:Point/gml:pos", ns).text.split())

    points = ElementTree.parse(os.path.join(extractdir, 'Navaid.BASELINE'))

    for point in points.findall("adrmsg:hasMember/aixm:Navaid", ns):
        all_points[point.find("gml:identifier", ns).text] = tuple(float(x) for x in point.find("aixm:timeSlice/aixm:NavaidTimeSlice/aixm:location/aixm:ElevatedPoint/gml:pos", ns).text.split())

@lru_cache(None)
def make_polygon(airspace):
    polygons = []
    for component in airspace.findall("aixm:geometryComponent/aixm:AirspaceGeometryComponent/aixm:theAirspaceVolume/aixm:AirspaceVolume/aixm:contributorAirspace/aixm:AirspaceVolumeDependency/aixm:theAirspace", ns):
        key = component.attrib['{http://www.w3.org/1999/xlink}href'].split(':')[2]
        child = full_dict[key]
        for ats in child.findall("aixm:timeSlice/aixm:AirspaceTimeSlice", ns):
            new_d = ats.find("aixm:designator", ns)
            if new_d is not None:
                polygons.append(make_polygon(ats))
            else:
                for lr in ats.findall("aixm:geometryComponent/aixm:AirspaceGeometryComponent/aixm:theAirspaceVolume/aixm:AirspaceVolume/aixm:horizontalProjection/aixm:Surface/gml:patches/gml:PolygonPatch/gml:exterior/gml:LinearRing", ns):
                    coords = []
                    for point in lr.iter():
                        if point.tag in ('{%s}pos' % (ns['gml']),
                                         '{%s}pointProperty' % (ns['gml'])):
                            if point.tag.endswith('pos'):
                                coords.append([float(x) for x in point.text.split()])
                            else:
                                coords.append(all_points[point.attrib['{http://www.w3.org/1999/xlink}href'].split(':')[2]])
                    polygons.append(Polygon([(lon, lat) for lat, lon in coords]))

    return(cascaded_union(polygons))

def get_area(name, type_ = None):
    polygon = None

    for airspace in tree.findall('adrmsg:hasMember/aixm:Airspace', ns):
        for ts in airspace.findall("aixm:timeSlice/aixm:AirspaceTimeSlice", ns):

            designator = ts.find("aixm:designator", ns)

            if (designator is not None and
                (designator.text == name) and
                (type_ is None or ts.find("aixm:type", ns).text == type_)):

                polygon = make_polygon(ts)
                break

    return polygon

def create_area(text):

    text = text.upper().split('/')
    sectorname = text[0]
    sectortype = None

    if len(text) > 1:
        sectortype = text[1]

    polygon = get_area(sectorname, sectortype)
    if polygon is None:
        return False, f'Sector name {sectorname} unknown'

    if sectortype:
        sectorname = f"{sectortype}_{sectorname}"

    areafilter.defineArea(sectorname, 'POLYALT', np.ravel(
        list((c[1], c[0]) for c in polygon.exterior.coords)))

    return True, f'AREA {sectorname} defined'
