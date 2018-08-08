#!/usr/bin/python2

r'''A module that provides a 'cameramodel' class to read/write/manipulate camera
models.

'''

import sys
import numpy as np
import numbers
import ast

import optimizer
import poseutils



def _validateExtrinsics(e):
    r'''Raises an exception if the given extrinsics are invalid'''

    # Internal extrinsic representation is a 6-long array
    try:
        N = len(e)
    except:
        raise Exception("Valid extrinsics are an iterable of len 6")


    if N != 6:
        raise Exception("Valid extrinsics are an iterable of len 6")

    for x in e:
        if not isinstance(x, numbers.Number):
            raise Exception("All extrinsics elements should be numeric, but '{}' isn't".format(x))

    return True


def _validateIntrinsics(i, MMt):
    r'''Raises an exception if the given intrinsics, covariances are invalid'''

    try:
        N = len(i)
    except:
        raise Exception("Valid intrinsics are an iterable of len 2")


    if N != 2:
        raise Exception("Valid intrinsics are an iterable of len 2")

    distortion_model = i[0]
    intrinsics       = i[1]

    # If this fails, I keep the exception and let it fall through
    Ndistortions_want = optimizer.getNdistortionParams(distortion_model)

    try:
        Ndistortions_have = len(intrinsics) - 4
    except:
        raise Exception("Valid intrinsics are (distortion_model, intrinsics) where 'intrinsics' is an iterable with a length")

    if Ndistortions_want != Ndistortions_have:
        raise Exception("Mismatched Ndistortions. Got {}, but model {} must have {}".format(Ndistortions_have,distortion_model,Ndistortions_want))

    for x in intrinsics:
        if not isinstance(x, numbers.Number):
            raise Exception("All intrinsics elements should be numeric, but '{}' isn't".format(x))


    if MMt is None:
        return True;

    Nintrinsics = len(intrinsics)

    try:
        s = MMt.shape
        s0 = s[0]
        s1 = s[1]
    except:
        raise Exception("A valid covariance is an (Nintrinsics,Nintrinsics) positive-semi-definite matrix")

    if not (len(s) == 2 and s0 == Nintrinsics and s1 == Nintrinsics):
        raise Exception("A valid covariance is an (Nintrinsics,Nintrinsics) positive-semi-definite matrix")

    if np.linalg.norm(MMt - MMt.transpose()) > 1e-9:
        raise Exception("A valid covariance is an (Nintrinsics,Nintrinsics) positive-semi-definite matrix; this one isn't even symmetric")

    # surely computing ALL the eigenvalues is overkill for just figuring out if
    # this thing is positive-semi-definite or not?
    try:
        eigenvalue_smallest = np.linalg.eigvalsh(MMt)[0]
    except:
        raise Exception("A valid covariance is an (Nintrinsics,Nintrinsics) positive-semi-definite matrix; couldn't compute eigenvalues")

    if eigenvalue_smallest < -1e-9:
        raise Exception("A valid covariance is an (Nintrinsics,Nintrinsics) positive-semi-definite matrix; this one isn't positive-semi-definite; smallest eigenvalue: {}".format(eigenvalue_smallest))

    return True


def _validateDimensions(d):
    r'''Raises an exception if the given dimensions are invalid'''

    # need two integers
    try:
        N = len(d)
        if N != 2:
            raise Exception()
        if d[0] <= 0 or d[1] <= 0:
            raise Exception()
        if d[0] != int(d[0]) or d[1] != int(d[1]):
            raise Exception()
    except:
        raise Exception("Dimensions must be an iterable of two positive integers")

    return True


class cameramodel(object):
    r'''A class that encapsulates an extrinsic,intrinsic model of a single camera

    For ONE camera this class represents

    - The intrinsics: parameters internal to this camera. These do not change as
      the camera moves around in space. These include

      - The 4 pinhole-camera parameters: focal lengths, coordinates of the
        center pixel

      - Some representation of the camera distortion. Multiple distortion models
        are supported. mrcal.optimizer.getSupportedDistortionModels() returns a
        list of supported models.

    - The extrinsics: the pose of this camera in respect to SOME reference
      coordinate system. The meaning of this coordinate system is defined by the
      user of this class: this class itself does not care

    - Optionally, some covariances that represent this camera model as a
      probabilistic quantity: the parameters are all gaussian, with mean at the
      given values, and with some covariance. These can be missing or None, in
      which case they will be assumed to be 0: no uncertainty exists.

    This class provides facilities to read/write models, and to get/set the
    various parameters.

    The format of a .cameramodel file is a python dictionary that we eval. A
    sample valid .cameramodel:

        # generated with ...
        { 'distortion_model': 'DISTORTION_OPENCV8',

          # intrinsics are fx,fy,cx,cy,distortion0,distortion1,....
          'intrinsics': [1766.0712405930,
                         1765.8925266865,
                         1944.0664501036,
                         1064.5231421210,
                         2.1648025156,
                         -1.1851581377,
                         -0.0000931342,
                         0.0007782462,
                         -0.2351910903,
                         2.4460295029,
                         -0.6697132481,
                         -0.6284355415],

          # extrinsics are rt_fromref
          'extrinsics': [0,0,0,0,0,0],
          'dimensions': [3840,2160]
        }

    '''

    def _write(self, f, note=None):
        r'''Writes out this camera model to an open file'''

        if note is not None:
            f.write('# ' + note + '\n')

        _validateIntrinsics(self._intrinsics,
                            self._covariance_intrinsics)
        _validateExtrinsics(self._extrinsics)

        # I write this out manually instead of using repr for the whole thing
        # because I want to preserve key ordering
        f.write("{\n")
        f.write("    'distortion_model':  '{}',\n".format(self._intrinsics[0]))
        f.write("\n")

        N = len(self._intrinsics[1])
        f.write("    # intrinsics are fx,fy,cx,cy,distortion0,distortion1,....\n")
        f.write(("    'intrinsics': [" + (" {:.10f}," * N) + "],\n").format(*self._intrinsics[1]))
        f.write("\n")

        if self._covariance_intrinsics is not None:
            distortion_model = self._intrinsics[0]
            Ndistortions_want = optimizer.getNdistortionParams(distortion_model)
            Nintrinsics = Ndistortions_want+4

            f.write( r'''    # The intrinsics are represented as a probabilistic quantity: the parameters
    # are all gaussian, with mean at the given values, and with some covariance.
''')
            f.write("    'covariance_intrinsics': [\n")
            for row in self._covariance_intrinsics:
                f.write(("    [" + (" {:.10f}," * Nintrinsics) + "],\n").format(*row))
            f.write("],\n\n")

        N = len(self._extrinsics)
        f.write("    # extrinsics are rt_fromref\n")
        f.write(("    'extrinsics': [" + (" {:.10f}," * N) + "],\n").format(*self._extrinsics))
        f.write("\n")

        N = 2
        f.write(("    'dimensions': [" + (" {:d}," * N) + "]\n").format(*(int(x) for x in self._dimensions)))
        f.write("}\n")


    def _read_into_self(self, f):
        r'''Reads in a model from an open file'''

        s = ''.join(f)

        try:
            model = ast.literal_eval(s)
        except:
            name = f.name
            if name is None:
                sys.stderr.write("Failed to parse cameramodel!\n")
            else:
                sys.stderr.write("Failed to parse cameramodel '{}'\n".format(name))
            raise

        keys_required = set(('distortion_model',
                             'intrinsics',
                             'extrinsics',
                             'dimensions'))
        keys_received = set(model.keys())
        if keys_received < keys_required:
            raise Exception("Model must have at least these keys: '{}'. Instead I got '{}'". \
                            format(keys_required, keys_received))

        if 'covariance_intrinsics' in model:
            covariance_intrinsics = np.array(model['covariance_intrinsics'])
        else:
            covariance_intrinsics = None

        intrinsics = (model['distortion_model'], model['intrinsics'])
        _validateIntrinsics(intrinsics, covariance_intrinsics)
        _validateExtrinsics(model['extrinsics'])
        _validateDimensions(model['dimensions'])

        self._intrinsics            = intrinsics
        self._covariance_intrinsics = covariance_intrinsics
        self._extrinsics            = model['extrinsics']
        self._dimensions            = model['dimensions']


    def __init__(self, file_or_model=None, **kwargs):
        r'''Initializes a new camera-model object

        If file_or_model is not None: we read the camera model from a filename,
        a pre-opened file or from another camera model (copy constructor)

        If f is None and kwargs is empty: we init the model with invalid
        intrinsics (None initially; will need to be set later), and identity
        extrinsics

        if f is none and we have kwargs:

        - 'intrinsics'
        - Exactly ONE of the following for theextrinsics:
          - 'extrinsics_Rt_toref'
          - 'extrinsics_Rt_fromref'
          - 'extrinsics_rt_toref'
          - 'extrinsics_rt_fromref'
        - 'dimensions'
        - 'covariance_intrinsics', optionally

        '''

        if len(kwargs) == 0:
            if file_or_model is None:
                self._extrinsics = np.zeros(6)
                self._intrinsics = None

            elif type(file_or_model) is cameramodel:
                import copy
                self._dimensions            = copy.deepcopy(file_or_model._dimensions)
                self._extrinsics            = copy.deepcopy(file_or_model._extrinsics)
                self._intrinsics            = copy.deepcopy(file_or_model._intrinsics)
                self._covariance_intrinsics = copy.deepcopy(file_or_model._covariance_intrinsics)


            elif type(file_or_model) is str:
                with open(file_or_model, 'r') as openedfile:
                    self._read_into_self(openedfile)

            else:
                self._read_into_self(file_or_model)

        else:
            if file_or_model is not None:
                raise Exception("We have kwargs AND file_or_model. These are supposed to be mutually exclusive")

            keys_remaining = set( kwargs.keys() )

            if 'intrinsics' not in kwargs:
                raise Exception("No file_or_model was given, so we MUST have gotten an 'intrinsics' kwarg")
            if 'dimensions' not in kwargs:
                raise Exception("No file_or_model was given, so we MUST have gotten a 'dimensions' kwarg")
            keys_remaining -= set(('intrinsics', 'dimensions'))

            extrinsics_keys = set(('extrinsics_Rt_toref',
                                   'extrinsics_Rt_fromref',
                                   'extrinsics_rt_toref',
                                   'extrinsics_rt_fromref'))
            extrinsics_got = keys_remaining.intersection(extrinsics_keys)
            if len(extrinsics_got) != 1:
                raise Exception("No file_or_model was given, so we MUST have gotten one of {}".format(extrinsics_keys))
            keys_remaining -= extrinsics_keys
            if keys_remaining:
                if not keys_remaining == set(('covariance_intrinsics',)):
                    raise Exception("No file_or_model was given, so we MUST have gotten 'intrinsics', 'extrinsics_...', 'dimensions' and MAYBE 'covariance_intrinsics'. Questionable keys: '{}'".format(keys_remaining))


            self.intrinsics(kwargs['intrinsics'])
            self.dimensions(kwargs['dimensions'])
            if not ('covariance_intrinsics' in kwargs and kwargs['covariance_intrinsics'] is not None):
                self._covariance_intrinsics = None
            else:
                self.covariance_intrinsics(kwargs['covariance_intrinsics'])

            if 'extrinsics_Rt_toref'   in kwargs: self.extrinsics_Rt(True,  kwargs['extrinsics_Rt_toref'  ])
            if 'extrinsics_Rt_fromref' in kwargs: self.extrinsics_Rt(False, kwargs['extrinsics_Rt_fromref'])
            if 'extrinsics_rt_toref'   in kwargs: self.extrinsics_rt(True,  kwargs['extrinsics_rt_toref'  ])
            if 'extrinsics_rt_fromref' in kwargs: self.extrinsics_rt(False, kwargs['extrinsics_rt_fromref'])



    def write(self, f, note=None):
        r'''Writes out this camera model

        We write to the given filename or a given pre-opened file.

        '''

        if type(f) is str:
            with open(f, 'w') as openedfile:
                self._write( openedfile, note )

        else:
            self._write( f, note )


    def intrinsics(self, i=None):
        r'''Get or set the intrinsics in this model

        if i is None: this is a getter; otherwise a setter.

        i is a tuple (distortion_model, parameters):

        - distortion_model is a string for the specific distortion model we're
          using. mrcal.optimizer.getSupportedDistortionModels() returns a list
          of supported models.

        - parameters is a numpy array of distortion parameters. The first 4
          values are the pinhole-camera parameters: (fx,fy,cx,cy). The following
          values represent the lens distortion. The number and meaning of these
          parameters depends on the distortion model we're using

        Note that this function resets the covariance, if we have one.

        '''

        if i is None:
            return self._intrinsics

        self._covariance_intrinsics = None
        _validateIntrinsics(i, self._covariance_intrinsics)
        self._intrinsics = i


    def extrinsics_rt(self, toref, rt=None):
        r'''Get or set the extrinsics in this model

        This function represents the pose as a 6-long numpy array that contains
        a 3-long Rodrigues rotation followed by a 3-long translation in the last
        row:

          r = rt[:3]
          t = rt[3:]
          R = cv2.Rodrigues(r)[0]

        The transformation is b <-- R*a + t:

          import numpysane as nps
          b = nps.matmult(a, nps.transpose(R)) + t

        if rt is None: this is a getter; otherwise a setter.

        toref is a boolean. if toref: then rt maps points in the coord system of
        THIS camera to the reference coord system. Otherwise in the opposite
        direction

        '''

        # The internal representation is rt_fromref

        if rt is None:
            # getter
            if not toref:
                return self._extrinsics
            return poseutils.invert_rt(self._extrinsics)


        # setter
        if not toref:
            self._extrinsics = rt
            return True

        self._extrinsics = poseutils.invert_rt(rt)
        return True


    def extrinsics_Rt(self, toref, Rt=None):
        r'''Get or set the extrinsics in this model

        This function represents the pose as a shape (4,3) numpy array that
        contains a (3,3) rotation matrix, followed by a (1,3) translation in the
        last row:

          R = Rt[:3,:]
          t = Rt[ 3,:]

        The transformation is b <-- R*a + t:

          import numpysane as nps
          b = nps.matmult(a, nps.transpose(R)) + t

        if Rt is None: this is a getter; otherwise a setter.

        toref is a boolean. if toref: then Rt maps points in the coord system of
        THIS camera to the reference coord system. Otherwise in the opposite
        direction

        '''

        # The internal representation is rt_fromref

        if Rt is None:
            # getter
            rt_fromref = self._extrinsics
            Rt_fromref = poseutils.Rt_from_rt(rt_fromref)
            if not toref:
                return Rt_fromref
            return poseutils.invert_Rt(Rt_fromref)


        # setter
        if toref:
            Rt_fromref = poseutils.invert_Rt(Rt)
            self._extrinsics = poseutils.rt_from_Rt(Rt_fromref)
            return True

        self._extrinsics = poseutils.rt_from_Rt(Rt)
        return True


    def dimensions(self, d=None):
        r'''Get or set the imager dimensions in this model

        if d is None: this is a getter; otherwise a setter.

        d is some sort of iterable of two numbers.

        The dimensions aren't used for very much and 99% of the time they can be
        omitted.

        '''

        if d is None:
            return self._dimensions

        _validateDimensions(d)
        self._dimensions = d


    def covariance_intrinsics(self, c=None):
        r'''Get or set the intrinsics covariance in this model

        if c is None: this is a getter; otherwise a setter.

        c is a numpy array, or something I can make a numpy array from

        '''

        if c is None:
            return self._covariance_intrinsics

        _validateIntrinsics(self._intrinsics, c)
        self._covariance_intrinsics = c


    def set_cookie(self, cookie):
        r'''Store some arbitrary cookie for somebody to use later

        This data means nothing to me, but the caller may want it'''
        self._cookie = cookie


    def get_cookie(self):
        r'''Retrive some arbitrary cookie from an earlier set_cookie

        This data means nothing to me, but the caller may want it'''
        try:
            return self._cookie
        except:
            return None
