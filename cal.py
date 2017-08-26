#!/usr/bin/python2

import sys
sys.path[:0] = ('/home/dima/projects/numpysane/',)

import numpy as np
import numpysane as nps
import gnuplotlib as gp
import cv2
import re
import glob
import cPickle as pickle

sys.path[:0] = ('build/lib.linux-x86_64-2.7/',)
import mrcal

re_f = '[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
re_u = '\d+'
re_d = '[-+]?\d+'
re_s = '.+'


@nps.broadcast_define( ((3,3),),
                       (3,), )
def Rodrigues_tor_broadcasted(R):
    return cv2.Rodrigues(R)[0].ravel()


@nps.broadcast_define( ((3,),),
                       (3,3), )
def Rodrigues_toR_broadcasted(r):
    return cv2.Rodrigues(r)[0]

def align3d_procrustes(A, B):
    r"""Given two sets of 3d points in numpy arrays of shape (3,N), find the optimal
rotation to align these sets of points. I.e. minimize

  E = sum( norm2( a_i - (R b_i + t)))

We can expand this to get

  E = sum( norm2(a_i) - 2 inner(a_i, R b_i + t ) + norm2(b_i) + 2 inner(R b_i,t) + norm2(t) )

ignoring factors not depending on the optimization variables (R,t)

  E ~ sum( - 2 inner(a_i, R b_i + t ) + 2 inner(R b_i,t) + norm2(t) )

  dE/dt = sum( - 2 a_i + 2 R b_i + 2 t ) = 0
  -> sum(a_i) = R sum(b_i) + N t -> t = mean(a) - R mean(b)

I can shift my a_i and b_i so that they have 0-mean. In this case, the optimal t
= 0 and

  E ~ sum( inner(a_i, R b_i )  )

This is the classic procrustes problem

  E = tr( At R B ) = tr( R B At ) = tr( R U S Vt ) = tr( Vt R U S )

So the critical points are at Vt R U = I and R = V Ut, modulo a tweak to make
sure that R is in SO(3) not just in SE(3)

    """

    # allow (3,N) and (N,3)
    if A.shape[0] != 3 and A.shape[1] == 3:
        A = nps.transpose(A)
    if B.shape[0] != 3 and B.shape[1] == 3:
        B = nps.transpose(B)

    M = nps.matmult(               B - np.mean(B, axis=-1)[..., np.newaxis],
                     nps.transpose(A - np.mean(A, axis=-1)[..., np.newaxis]) )
    U,S,Vt = np.linalg.svd(M)

    R = nps.matmult(U, Vt)

    # det(R) is now +1 or -1. If it's -1, then this contains a mirror, and thus
    # is not a physical rotation. I compensate by negating the least-important
    # pair of singular vectors
    if np.linalg.det(R) < 0:
        U[:,2] *= -1
        R = nps.matmult(U, Vt)

    # I wanted V Ut, not U Vt
    R = nps.transpose(R)

    # Now that I have my optimal R, I compute the optimal t. From before:
    #
    #   t = mean(a) - R mean(b)
    t = np.mean(A, axis=-1)[..., np.newaxis] - nps.matmult( R, np.mean(B, axis=-1)[..., np.newaxis] )
    t = t.ravel()

    return R,t

def get_full_object(W, H, dot_spacing):
    r'''Returns the geometry of the calibration object in its own coordinate frame.
Shape is (H,W,2). I.e. the x index varies the fastest and each xyz coordinate
lives at (y,x,:)

    '''

    xx,yy       = np.meshgrid( np.arange(W,dtype=float), np.arange(H,dtype=float))
    full_object = nps.glue(nps.mv( nps.cat(xx,yy), 0, -1),
                           np.zeros((H,W,1)),
                           axis=-1) # shape (H,W,3)
    return full_object * dot_spacing

def read_observations_from_file__old_dot(filename, which):
    r"""Given a xxx.dots file, read the observations into a numpy array. Returns this
numpy array and a list of metadata.

The array has axes: (iframe, idot_y, idot_x, idot2d_xy)

So as an example, the observed pixel coord of the dot (3,4) in frame index 5 is
the 2-vector dots[5,3,4,:]

The metadata is a dictionary, containing the dimensions of the imager, and the
indices of frames that the numpy array contains

    """

    with open(filename, 'r') as f:
        for l in f:
            if re.match('# Format: jplv$',
                        l):
                break
        else:
            raise Exception('No explicit "Format: jplv" when reading {}'.format(filename))

        # Data. Axes: (iframe, idot_y, idot_x, idot2d_xy)
        # So the observed pixel coord of the dot (3,4) in frame index 5 is
        # the 2-vector dots[5,3,4,:]
        dots       = np.array( (), dtype=float)
        metadata   = {'frames': [],
                      'imager_size': None,
                      'dot_spacing': None}
        cur_frame  = None
        cur_iframe = None

        for l in f:
            if l[0] == '\n' or l[0] == '#':
                continue

            m = re.match('IMAGE ({u}) ({u})'.format(u=re_u),
                         l)
            if m:
                if metadata['imager_size'] is not None:
                    raise Exception('Got line "{}", but already have width, height'.format(l))
                metadata['imager_size'] = (int(m.group(1)), int(m.group(2)))
                continue

            m = re.match('DOT ({s}) (stcal-({u})-({s}).pnm) FIX ({u}) ({u}) ({u}) ({f}) ({f}) IMG ({f}) ({f})'.format(f=re_f, u=re_u, d=re_d, s=re_s), l)
            if m:
                if which != m.group(1):
                    raise Exception("Reading file {}: expected '{}' frames, but got '{}". \
                                    format(filename, which, m.group(1)))
                if which != m.group(4):
                    raise Exception("Reading file {}: expected '{}' frames, but got image file '{}". \
                                    format(filename, which, m.group(2)))
                frame  = int(m.group(3))
                iframe = int(m.group(5))
                idot   = (int(   m.group(6)),  int(  m.group(7)))
                dot3d  = (float( m.group(8)),  float(m.group(9)))
                dot2d  = np.array(( float(m.group(10)), float(m.group(11))))

                if cur_iframe == iframe and \
                   cur_frame  != frame:
                    raise Exception('frame changed, but iframe did not')
                if cur_frame  == frame and \
                   cur_iframe != iframe:
                    raise Exception('iframe changed, but frame did not')
                if cur_iframe is None and iframe != 0:
                    raise Exception('iframe should start at 0')

                if cur_iframe != iframe:
                    if cur_iframe is not None and cur_iframe+1 != iframe:
                        raise Exception('non-consecutive frame index...')
                    if cur_frame is not None and cur_frame >= frame:
                        raise Exception('non-increasing frame number...')

                    cur_frame,cur_iframe = frame,iframe
                    dots = nps.glue( dots,
                                     np.zeros((10,10,2), dtype=float) - 1,
                                     axis=-4 )
                    metadata['frames'].append(frame)

                dot_spacing = np.array(dot3d, dtype=float) / np.array(idot, dtype=float)
                if metadata['dot_spacing'] is None:
                    metadata['dot_spacing'] = dot_spacing
                else:
                    if np.max( np.abs(metadata['dot_spacing'] - dot_spacing) ) > 1e-4:
                        raise Exception("Inconsistent dot spacing. Previously saw {} but now have {}". \
                                        format(metadata['dot_spacing'], dot_spacing))

                dots[-1, idot[1]-1, idot[0]-1,:] = dot2d
                continue

            raise Exception('Got unknown line "{}"'.format(l))

    return dots, metadata

def read_observations_from_file__asciilog_dots(filename):
    r"""Given a xxx.dots file, read the observations into a numpy array. Returns this
numpy array and a list of metadata.

The array has axes: (idot_y, idot_x, idot2d_xy)

So as an example, the observed pixel coord of the dot (3,4) is the 2-vector
dots[3,4,:]

The metadata is a dictionary, containing the dimensions of the imager, and the
indices of frames that the numpy array contains

    """

    # Data. Axes: (idot_y, idot_x, idot2d_xy)
    # So the observed pixel coord of the dot (3,4) is
    # the 2-vector dots[3,4,:]
    dots     = np.array( (), dtype=float)
    metadata = {'dot_spacing': None}

    point_index = 0

    with open(filename, 'r') as f:

        l = next(f)
        if l != '# path fixture_size_m fixture_space_m fixture_cols fixture_rows num_dots_detected dot_fixture_col dot_fixture_row dot_fixture_physical_x dot_fixture_physical_y dot_image_col dot_image_row\n':
            raise Exception("Unexpected legend in '{}".format(filename))
        l = next(f)

        m = re.match('.* ({f}) ({f}) 10 10 ({d}) - - - - - -\n$'.format(f=re_f, d=re_d), l)
        if m is None:
            raise Exception("Unexpected metadata in '{}".format(filename))
        metadata['dot_spacing'] = float(m.group(2))

        # I only accept complete observations of the cal board for now
        Ndetected = int(m.group(3))
        if Ndetected != 100:
            return None,None

        dots = np.zeros((10,10,2), dtype=float)

        for l in f:

            lf = l.split()
            idot_x,idot_y     = [int(x)   for x in lf[6 :8 ]]
            dot2d_x, dot2d_y  = [float(x) for x in lf[10:12]]

            # I only accept complete observations of the cal board for now
            idot_y_want = int(point_index / 10)
            idot_x_want = point_index - idot_y_want*10
            if idot_x != idot_x_want or idot_y != idot_y_want:
                return None,None
            point_index += 1

            dots[idot_y,idot_x,:] = (dot2d_x,dot2d_y)

    return dots, metadata

def estimate_local_calobject_poses( dots, dot_spacing, focal, imager_size ):
    r"""Given observations, and an estimate of camera intrinsics (focal lengths,
imager size) computes an estimate of the pose of the calibration object in
respect to the camera. This assumes a pinhole camera, and all the work is done
by the solvePnP() openCV call.

The observations are given in a numpy array with axes:

  (iframe, idot_x, idot_y, idot2d_xy)

So as an example, the observed pixel coord of the dot (3,4) in frame index 5 is
the 2-vector dots[5,3,4,:]

Missing observations are given as negative pixel coords

    """

    camera_matrix = np.array((( focal[0], 0,        imager_size[0]/2), \
                              (        0, focal[1], imager_size[1]/2), \
                              (        0,        0,                 1)))

    full_object = get_full_object(10, 10, dot_spacing)

    # look through each frame
    Rall = np.array(())
    tall = np.array(())

    for d in dots:
        d = nps.transpose(nps.clump( nps.mv( nps.glue(d, full_object, axis=-1), -1, -3 ), n=2 ))
        # d is (100,5); each row is an xy pixel observation followed by the xyz
        # coord of the point in the calibration object. I pick off those rows
        # where the observations are both >= 0. Result should be (N,5) where N
        # <= 100
        i = (d[..., 0] >= 0) * (d[..., 1] >= 0)
        d = d[i,:]

        observations = d[:,:2]
        ref_object   = d[:,2:]
        result,rvec,tvec = cv2.solvePnP(ref_object  [..., np.newaxis],
                                        observations[..., np.newaxis],
                                        camera_matrix, None)
        if not result:
            raise Exception("solvePnP failed!")
        if tvec[2] <= 0:
            raise Exception("solvePnP says that tvec.z <= 0. Maybe needs a flip, but please examine this")

        R,_ = cv2.Rodrigues(rvec)
        Rall = nps.glue(Rall, R,            axis=-3)
        tall = nps.glue(tall, tvec.ravel(), axis=-2)

    return Rall,tall

def estimate_camera_pose( calobject_poses, dots, dot_spacing, metadata ):

    if sorted(metadata.keys()) != ['left', 'right']:
        raise Exception("metadata dict has unknown keys: {}".format(metadata.keys()))

    if 'frames' in metadata['left' ]:
        f0 = metadata['left' ]['frames']
        f1 = metadata['right']['frames']
        if f0 != f1:
            raise Exception("Inconsistent frame sets. This is from my quick implementation. Please fix")
        Nframes = len(f0)
    else:
        Nframes = dots['left'].shape[0]

    A = np.array(())
    B = np.array(())

    R0 = calobject_poses['left' ][0]
    t0 = calobject_poses['left' ][1]
    R1 = calobject_poses['right'][0]
    t1 = calobject_poses['right'][1]

    full_object = get_full_object(10, 10, dot_spacing)

    for iframe in xrange(Nframes):

        # d looks at one frame and has shape (10,10,7). Each row is
        #   xy pixel observation in left camera
        #   xy pixel observation in right camera
        #   xyz coord of dot in the calibration object coord system
        d = nps.glue( dots['left'][iframe], dots['right'][iframe], full_object, axis=-1 )

        # squash dims so that d is (100,7)
        d = nps.transpose(nps.clump(nps.mv(d, -1, -3), n=2))

        # I pick out those points that have observations in both frames
        i = (d[..., 0] >= 0) * (d[..., 1] >= 0) * (d[..., 2] >= 0) * (d[..., 3] >= 0)
        d = d[i,:]

        # ref_object is (3,N)
        ref_object = nps.transpose( d[:,4:] )

        A = nps.glue(A, nps.transpose(nps.matmult( R0[iframe], ref_object)) + t0[iframe],
                     axis = -2)
        B = nps.glue(B, nps.transpose(nps.matmult( R1[iframe], ref_object)) + t1[iframe],
                     axis = -2)


    return align3d_procrustes(A, B)

def project_points(intrinsics, extrinsics, frames, observations, dot_spacing):
    r'''Takes in the same arguments as mrcal.optimize(), and returns all the
reprojection errors'''

    object_ref = get_full_object(10, 10, dot_spacing)
    Rf = Rodrigues_toR_broadcasted(frames[:,:3])
    Rf = nps.mv(Rf,           0, -5)
    tf = nps.mv(frames[:,3:], 0, -5)

    # object in the cam0 coord system. shape=(Nframes, 1, 10, 10, 3)
    object_cam0 = nps.matmult( object_ref, nps.transpose(Rf)) + tf

    Rc = Rodrigues_toR_broadcasted(extrinsics[:,:3])
    Rc = nps.mv(Rc,               0, -4)
    tc = nps.mv(extrinsics[:,3:], 0, -4)

    # object in the OTHER camera coord systems. shape=(Nframes, Ncameras-1, 10, 10, 3)
    object_cam_others = nps.matmult( object_cam0, nps.transpose(Rc)) + tc

    # object in the ALL camera coord systems. shape=(Nframes, Ncameras, 10, 10, 3)
    object_cam = nps.glue(object_cam0, object_cam_others, axis=-4)

    # I now project all of these
    intrinsics = nps.mv(intrinsics, 0, -4)

    # projected points. shape=(Nframes, Ncameras, 10, 10, 2)
    projected = object_cam[..., :2] / object_cam[..., 2:] * intrinsics[..., :2] + intrinsics[..., 2:]
    return projected

look_at_old_dot_files = False
cache                 = 'dots.pickle'
read_cache            = True

focal_estimate    = 1950 # pixels
imager_w_estimate = 3904

calobject_poses = {}
dots            = {}
metadata        = {}


if look_at_old_dot_files:
    for fil in ('/home/dima/data/cal_data_2017_07_14/lfc4/stcal-left.dots',
                '/home/dima/data/cal_data_2017_07_14/lfc4/stcal-right.dots'):
        m = re.match( '.*-(left|right)\.dots$', fil)
        if not m: raise Exception("Can't tell if '{}' is left or right".format(fil))
        which = m.group(1)

        d,m             = read_observations_from_file__old_dot( fil, which )
        dots    [which] = d
        metadata[which] = m

        # I compute an estimate of the poses of the calibration object in the local
        # coord system of each camera for each frame. This is done for each frame
        # and for each camera separately. This isn't meant to be precise, and is
        # only used for seeding.
        #
        # I get rotation, translation such that R*calobject + t produces the
        # calibration object points in the coord system of the camera. Here R,t have
        # dimensions (N,3,3) and (N,3) respectively
        R,t = estimate_local_calobject_poses( dots[which],
                                              metadata[which]['dot_spacing'],
                                              (focal_estimate, focal_estimate),
                                              metadata[which]['imager_size'] )
        calobject_poses[which] = (R,t)

else:

    if read_cache:
        with open(cache, 'r') as f:
            dots,metadata = pickle.load(f)

    else:
        datadir = '/to_be_filed/datasets/2017-08-08-usv-swarm-test-3/output/calibration/stereo-2017-08-02-Wed-19-30-23/dots/opencv-only'
        pair = 0

        dotfiles_left  = sorted(glob.glob('{}/frame[0-9]*-pair{}-cam0.dots'.format(datadir,pair)))
        dotfiles_right = sorted(glob.glob('{}/frame[0-9]*-pair{}-cam1.dots'.format(datadir,pair)))
        dots['left' ] = np.array(())
        dots['right'] = np.array(())

        for fil_left,fil_right in zip(dotfiles_left,dotfiles_right):
            m = re.match( '.*/frame([0-9]+)-pair[01]-cam0.dots$', fil_left)
            if not m: raise Exception("Can't parse filename '{}'".format(fil_left))
            i_frame_left = int(m.group(1))

            m = re.match( '.*/frame([0-9]+)-pair[01]-cam1.dots$', fil_right)
            if not m: raise Exception("Can't parse filename '{}'".format(fil_right))
            i_frame_right = int(m.group(1))

            if i_frame_left != i_frame_right:
                raise Exception("Mismatched frames")

            d_left, m_left  = read_observations_from_file__asciilog_dots( fil_left )
            if d_left is None: continue

            d_right,m_right = read_observations_from_file__asciilog_dots( fil_right )
            if d_right is None: continue

            if 'left'  not in metadata:
                metadata['left' ] = m_left
            else:
                if m_left['dot_spacing'] != metadata['left' ]['dot_spacing']:
                    raise Exception("Inconsistent dot spacing")

            if 'right' not in metadata:
                metadata['right'] = m_right
            else:
                if m_right['dot_spacing'] != metadata['right' ]['dot_spacing']:
                    raise Exception("Inconsistent dot spacing")

            if m_left['dot_spacing'] != m_right['dot_spacing']:
                raise Exception("mismatched dot spacing")

            # neato. I have a full view of the object with both cameras
            dots['left' ] = nps.glue(dots['left' ], d_left,  axis = -4)
            dots['right'] = nps.glue(dots['right'], d_right, axis = -4)

        with open(cache, 'w') as f:
            pickle.dump( (dots,metadata), f, protocol=2)
        print "Wrote cache to '{}'".format(cache)
        sys.exit(1)



    for which in ('left','right'):
        # I compute an estimate of the poses of the calibration object in the local
        # coord system of each camera for each frame. This is done for each frame
        # and for each camera separately. This isn't meant to be precise, and is
        # only used for seeding.
        #
        # I get rotation, translation such that R*calobject + t produces the
        # calibration object points in the coord system of the camera. Here R,t have
        # dimensions (N,3,3) and (N,3) respectively
        metadata[which]['imager_size'] = (imager_w_estimate, imager_w_estimate)
        R,t = estimate_local_calobject_poses( dots[which],
                                              metadata[which]['dot_spacing'],
                                              (focal_estimate, focal_estimate),
                                              metadata[which]['imager_size'] )

        # these map FROM the coord system of the calibration object TO the coord
        # system of this camera
        calobject_poses[which] = (R,t)

if np.linalg.norm(metadata['left']['dot_spacing'] - metadata['right']['dot_spacing']) > 1e-5:
    raise Exception("Mismatched dot spacing")

# I now have a rough estimate of calobject poses in the coord system of each
# frame. One can think of these as two sets of point clouds, each attached to
# their camera. I can move around the two sets of point clouds to try to match
# them up, and this will give me an estimate of the relative pose of the two
# cameras in respect to each other. I need to set up the correspondences, and
# align3d_procrustes() does the rest
#
# I get transformations that map points in 1-Nth camera coord system to 0th
# camera coord system. R,t have dimensions (N-1,3,3) and (N-1,3) respectively
R,t = estimate_camera_pose( calobject_poses,
                            dots,
                            metadata['left']['dot_spacing'],
                            metadata )

# I now have estimates of all parameters, and can run the full optimization
Ncameras = 2
Nframes  = dots['left'].shape[0]


def get_intrinsics(which):
    if 'imager_size' not in metadata[which]:
        imager_w = imager_w_estimate
        imager_h = imager_w_estimate
    else:
        imager_w,imager_h = metadata[which]['imager_size']
    return np.array((focal_estimate, focal_estimate,
                     float(imager_w-1)/2.,
                     float(imager_h-1)/2.))

intrinsics = nps.cat(get_intrinsics('left'), get_intrinsics('right'))

# extrinsics should map FROM the ref coord system TO the coord system of the
# camera in question. This is backwards from what I have. To flip:
#
# R*x + t = x'    ->     x = Rt x' - Rt t
extrinsics = nps.atleast_dims( nps.glue( cv2.Rodrigues(nps.transpose(R))[0].ravel(),
                                         -nps.matmult( t, R ), axis=-1 ),
                               -2)

# frame poses should map FROM the frame coord system TO the ref coord system
# (camera 0). I have an estimate of these for each camera. Should merge them,
# but for now, let me simply take the camera0 estimate
allR,allt = calobject_poses['left']

frames = nps.glue( Rodrigues_tor_broadcasted(allR), allt, axis=-1 )

observations = nps.mv( nps.cat( dots['left'], dots['right']), 0, -4)

observations = np.ascontiguousarray(observations)

# for debugging
projected = project_points(intrinsics, extrinsics, frames, observations,
                           metadata['left']['dot_spacing'])
err = projected - observations


mrcal.optimize(intrinsics, extrinsics, frames, observations)

