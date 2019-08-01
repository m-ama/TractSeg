 # Copyright 2017 Division of Medical Image Computing, German Cancer Research Center (DKFZ)
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# from future import standard_library
# standard_library.install_aliases()
from builtins import object
import abc
from warnings import warn

"""
Copy part of code from https://github.com/MIC-DKFZ/batchgenerators needed for inference so we do not
need this dependency during inference. This way we can become windows compatible.
"""

class SingleThreadedAugmenter(object):
    """
    Use this for debugging custom transforms. It does not use a background thread and you can therefore easily debug
    into your augmentations. This should not be used for training. If you want a generator that uses (a) background
    process(es), use MultiThreadedAugmenter.
    Args:
        data_loader (generator or DataLoaderBase instance): Your data loader. Must have a .next() function and return
        a dict that complies with our data structure

        transform (Transform instance): Any of our transformations. If you want to use multiple transformations then
        use our Compose transform! Can be None (in that case no transform will be applied)
    """
    def __init__(self, data_loader, transform):
        self.data_loader = data_loader
        self.transform = transform

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self.data_loader)
        item = self.transform(**item)
        return item


def zero_mean_unit_variance_normalization(data, per_channel=True, epsilon=1e-7):
    for b in range(data.shape[0]):
        if per_channel:
            for c in range(data.shape[1]):
                mean = data[b, c].mean()
                std = data[b, c].std() + epsilon
                data[b, c] = (data[b, c] - mean) / std
        else:
            mean = data[b].mean()
            std = data[b].std() + epsilon
            data[b] = (data[b] - mean) / std
    return data


class AbstractTransform(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def __call__(self, **data_dict):
        raise NotImplementedError("Abstract, so implement")

    def __repr__(self):
        ret_str = str(type(self).__name__) + "( " + ", ".join(
            [key + " = " + repr(val) for key, val in self.__dict__.items()]) + " )"
        return ret_str


class Compose(AbstractTransform):
    """Composes several transforms together.

    Args:
        transforms (list of ``Transform`` objects): list of transforms to compose.

    Example:
        >>> transforms.Compose([
        >>>     transforms.CenterCrop(10),
        >>>     transforms.ToTensor(),
        >>> ])
    """

    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, **data_dict):
        for t in self.transforms:
            data_dict = t(**data_dict)
        return data_dict

    def __repr__(self):
        return str(type(self).__name__) + " ( " + repr(self.transforms) + " )"


class ZeroMeanUnitVarianceTransform(AbstractTransform):
    """ Zero mean unit variance transform

    Args:
        per_channel (bool): determines whether mean and std are computed for and applied to each color channel
        separately

        epsilon (float): prevent nan if std is zero, keep at 1e-7
    """

    def __init__(self, per_channel=True, epsilon=1e-7, data_key="data", label_key="seg"):
        self.data_key = data_key
        self.label_key = label_key
        self.epsilon = epsilon
        self.per_channel = per_channel

    def __call__(self, **data_dict):
        data_dict[self.data_key] = zero_mean_unit_variance_normalization(data_dict[self.data_key], self.per_channel,
                                                                         self.epsilon)
        return data_dict


class NumpyToTensor(AbstractTransform):
    def __init__(self, keys=None, cast_to=None, pin_memory=False):
        """Utility function for pytorch. Converts data (and seg) numpy ndarrays to pytorch tensors
        :param keys: specify keys to be converted to tensors. If None then all keys will be converted
        (if value id np.ndarray). Can be a key (typically string) or a list/tuple of keys
        :param cast_to: if not None then the values will be cast to what is specified here. Currently only half, float
        and long supported (use string)
        """
        if not isinstance(keys, (list, tuple)):
            keys = [keys]
        self.keys = keys
        self.cast_to = cast_to
        self.pin_memory = pin_memory

    def cast(self, tensor):
        if self.cast_to is not None:
            if self.cast_to == 'half':
                tensor = tensor.half()
            elif self.cast_to == 'float':
                tensor = tensor.float()
            elif self.cast_to == 'long':
                tensor = tensor.long()
            else:
                raise ValueError('Unknown value for cast_to: %s' % self.cast_to)
        return tensor

    def __call__(self, **data_dict):
        import torch

        if self.keys is None:
            for key, val in data_dict.items():
                if isinstance(val, np.ndarray):
                    data_dict[key] = self.cast(torch.from_numpy(val))
                    if self.pin_memory:
                        data_dict[key] = data_dict[key].pin_memory()
        else:
            for key in self.keys:
                data_dict[key] = self.cast(torch.from_numpy(data_dict[key]))
                if self.pin_memory:
                    data_dict[key] = data_dict[key].pin_memory()

        return data_dict


class ResampleTransformLegacy(AbstractTransform):
    '''
    This is no longer part of batchgenerators, so we have an implementation here.
    CPU always 100% when using this, but batch_time on cluster not longer (1s)

    Downsamples each sample (linearly) by a random factor and upsamples to original resolution again (nearest neighbor)
    Info:
    * Uses scipy zoom for resampling.
    * Resamples all dimensions (channels, x, y, z) with same downsampling factor (like isotropic=True from linear_downsampling_generator_nilearn)
    Args:
        zoom_range (tuple of float): Random downscaling factor in this range. (e.g.: 0.5 halfs the resolution)
    '''

    def __init__(self, zoom_range=(0.5, 1)):
        self.zoom_range = zoom_range

    def __call__(self, **data_dict):
        data_dict['data'] = augment_linear_downsampling_scipy(data_dict['data'], zoom_range=self.zoom_range)
        return data_dict

def augment_linear_downsampling_scipy(data, zoom_range=(0.5, 1)):
    '''
    Downsamples each sample (linearly) by a random factor and upsamples to original resolution again (nearest neighbor)
    Info:
    * Uses scipy zoom for resampling. A bit faster than nilearn.
    * Resamples all dimensions (channels, x, y, z) with same downsampling factor (like isotropic=True from linear_downsampling_generator_nilearn)
    '''
    import random
    import scipy.ndimage
    import numpy as np

    zoom_range = list(zoom_range)
    zoom_range[1] += + 1e-6
    if zoom_range[0] >= zoom_range[1]:
        raise ValueError("First value of zoom_range must be smaller than second value.")

    dim = len(data.shape[2:])  # remove batch_size and nr_of_channels dimension
    for sample_idx in range(data.shape[0]):

        zoom = round(random.uniform(zoom_range[0], zoom_range[1]), 2)

        for channel_idx in range(data.shape[1]):
            img = data[sample_idx, channel_idx]
            img_down = scipy.ndimage.zoom(img, zoom, order=1)
            zoom_reverse = round(1. / zoom, 2)
            img_up = scipy.ndimage.zoom(img_down, zoom_reverse, order=0)

            if dim == 3:
                # cut if dimension got too long
                img_up = img_up[:img.shape[0], :img.shape[1], :img.shape[2]]

                # pad with 0 if dimension too small
                img_padded = np.zeros((img.shape[0], img.shape[1], img.shape[2]))
                img_padded[:img_up.shape[0], :img_up.shape[1], :img_up.shape[2]] = img_up

                data[sample_idx, channel_idx] = img_padded

            elif dim == 2:
                # cut if dimension got too long
                img_up = img_up[:img.shape[0], :img.shape[1]]

                # pad with 0 if dimension too small
                img_padded = np.zeros((img.shape[0], img.shape[1]))
                img_padded[:img_up.shape[0], :img_up.shape[1]] = img_up

                data[sample_idx, channel_idx] = img_padded
            else:
                raise ValueError("Invalid dimension size")

    return data


import numpy as np
from batchgenerators.augmentations.utils import create_zero_centered_coordinate_mesh
from batchgenerators.augmentations.utils import elastic_deform_coordinates
from batchgenerators.augmentations.utils import rotate_coords_3d
from batchgenerators.augmentations.utils import rotate_coords_2d
from batchgenerators.augmentations.utils import scale_coords
from batchgenerators.augmentations.utils import create_matrix_rotation_2d
from batchgenerators.augmentations.utils import create_matrix_rotation_x_3d
from batchgenerators.augmentations.utils import create_matrix_rotation_y_3d
from batchgenerators.augmentations.utils import create_matrix_rotation_z_3d
from batchgenerators.augmentations.utils import interpolate_img
from batchgenerators.augmentations.crop_and_pad_augmentations import random_crop as random_crop_aug
from batchgenerators.augmentations.crop_and_pad_augmentations import center_crop as center_crop_aug


def rotate_peaks(peaks, angle_x, angle_y, angle_z):
    # rot_matrix = np.identity(len(coords))
    rot_matrix = np.identity(3)
    rot_matrix = create_matrix_rotation_x_3d(angle_x, rot_matrix)
    rot_matrix = create_matrix_rotation_y_3d(angle_y, rot_matrix)
    rot_matrix = create_matrix_rotation_z_3d(angle_z, rot_matrix)
    peaks_rot = np.dot(peaks.reshape(3, -1).transpose(), rot_matrix).transpose().reshape(peaks.shape)
    return peaks_rot

def rotate_peaks_all(data, angle_x, angle_y, angle_z):
    # data: (9, x, y, [z])
    print("angle_x: {}".format(angle_x))
    print("angle_y: {}".format(angle_y))
    print("angle_z: {}".format(angle_z))

    peaks_rot = np.zeros(data.shape)
    for i in range(3):
        peaks_rot[i*3:(i+1)*3, ...] = rotate_peaks(data[i*3:(i+1)*3, ...], angle_x, angle_y, angle_z)
    return peaks_rot

def augment_spatial(data, seg, patch_size, patch_center_dist_from_border=30,
                    do_elastic_deform=True, alpha=(0., 1000.), sigma=(10., 13.),
                    do_rotation=True, angle_x=(0, 2 * np.pi), angle_y=(0, 2 * np.pi), angle_z=(0, 2 * np.pi),
                    do_scale=True, scale=(0.75, 1.25), border_mode_data='nearest', border_cval_data=0, order_data=3,
                    border_mode_seg='constant', border_cval_seg=0, order_seg=0, random_crop=True, p_el_per_sample=1,
                    p_scale_per_sample=1, p_rot_per_sample=1):
    dim = len(patch_size)
    seg_result = None
    if seg is not None:
        if dim == 2:
            seg_result = np.zeros((seg.shape[0], seg.shape[1], patch_size[0], patch_size[1]), dtype=np.float32)
        else:
            seg_result = np.zeros((seg.shape[0], seg.shape[1], patch_size[0], patch_size[1], patch_size[2]),
                                  dtype=np.float32)

    if dim == 2:
        data_result = np.zeros((data.shape[0], data.shape[1], patch_size[0], patch_size[1]), dtype=np.float32)
    else:
        data_result = np.zeros((data.shape[0], data.shape[1], patch_size[0], patch_size[1], patch_size[2]),
                               dtype=np.float32)

    if not isinstance(patch_center_dist_from_border, (list, tuple, np.ndarray)):
        patch_center_dist_from_border = dim * [patch_center_dist_from_border]

    for sample_id in range(data.shape[0]):
        coords = create_zero_centered_coordinate_mesh(patch_size)
        modified_coords = False

        if np.random.uniform() < p_el_per_sample and do_elastic_deform:
            a = np.random.uniform(alpha[0], alpha[1])
            s = np.random.uniform(sigma[0], sigma[1])
            coords = elastic_deform_coordinates(coords, a, s)
            modified_coords = True

        # a_x = -99
        # a_y = -99
        # a_z = -99

        if np.random.uniform() < p_rot_per_sample and do_rotation:
            if angle_x[0] == angle_x[1]:
                a_x = angle_x[0]
            else:
                a_x = np.random.uniform(angle_x[0], angle_x[1])
            if dim == 3:
                if angle_y[0] == angle_y[1]:
                    a_y = angle_y[0]
                else:
                    a_y = np.random.uniform(angle_y[0], angle_y[1])
                if angle_z[0] == angle_z[1]:
                    a_z = angle_z[0]
                else:
                    a_z = np.random.uniform(angle_z[0], angle_z[1])
                coords = rotate_coords_3d(coords, a_x, a_y, a_z)
            else:
                coords = rotate_coords_2d(coords, a_x)
            modified_coords = True

        if np.random.uniform() < p_scale_per_sample and do_scale:
            if np.random.random() < 0.5 and scale[0] < 1:
                sc = np.random.uniform(scale[0], 1)
            else:
                sc = np.random.uniform(max(scale[0], 1), scale[1])
            coords = scale_coords(coords, sc)
            modified_coords = True

        # now find a nice center location
        if modified_coords:
            for d in range(dim):
                if random_crop:
                    ctr = np.random.uniform(patch_center_dist_from_border[d],
                                            data.shape[d + 2] - patch_center_dist_from_border[d])
                else:
                    ctr = int(np.round(data.shape[d + 2] / 2.))
                coords[d] += ctr
            for channel_id in range(data.shape[1]):
                data_result[sample_id, channel_id] = interpolate_img(data[sample_id, channel_id], coords, order_data,
                                                                     border_mode_data, cval=border_cval_data)
            if seg is not None:
                for channel_id in range(seg.shape[1]):
                    seg_result[sample_id, channel_id] = interpolate_img(seg[sample_id, channel_id], coords, order_seg,
                                                                        border_mode_seg, cval=border_cval_seg,
                                                                        is_seg=True)
        else:
            if seg is None:
                s = None
            else:
                s = seg[sample_id:sample_id + 1]
            if random_crop:
                margin = [patch_center_dist_from_border[d] - patch_size[d] // 2 for d in range(dim)]
                d, s = random_crop_aug(data[sample_id:sample_id + 1], s, patch_size, margin)
            else:
                d, s = center_crop_aug(data[sample_id:sample_id + 1], patch_size, s)
            data_result[sample_id] = d[0]
            if seg is not None:
                seg_result[sample_id] = s[0]

        #todo important: change
        # data_result[sample_id] = rotate_peaks_all(data_result[sample_id], a_x, a_y, a_z)
        data_result[sample_id] = rotate_peaks_all(data_result[sample_id], 1, 0, 0)

    return data_result, seg_result


class SpatialTransform_Custom(AbstractTransform):
    """The ultimate spatial transform generator. Rotation, deformation, scaling, cropping: It has all you ever dreamed
    of. Computational time scales only with patch_size, not with input patch size or type of augmentations used.
    Internally, this transform will use a coordinate grid of shape patch_size to which the transformations are
    applied (very fast). Interpolation on the image data will only be done at the very end

    Args:
        patch_size (tuple/list/ndarray of int): Output patch size

        patch_center_dist_from_border (tuple/list/ndarray of int, or int): How far should the center pixel of the
        extracted patch be from the image border? Recommended to use patch_size//2.
        This only applies when random_crop=True

        do_elastic_deform (bool): Whether or not to apply elastic deformation

        alpha (tuple of float): magnitude of the elastic deformation; randomly sampled from interval

        sigma (tuple of float): scale of the elastic deformation (small = local, large = global); randomly sampled
        from interval

        do_rotation (bool): Whether or not to apply rotation

        angle_x, angle_y, angle_z (tuple of float): angle in rad; randomly sampled from interval. Always double check
        whether axes are correct!

        do_scale (bool): Whether or not to apply scaling

        scale (tuple of float): scale range ; scale is randomly sampled from interval

        border_mode_data: How to treat border pixels in data? see scipy.ndimage.map_coordinates

        border_cval_data: If border_mode_data=constant, what value to use?

        order_data: Order of interpolation for data. see scipy.ndimage.map_coordinates

        border_mode_seg: How to treat border pixels in seg? see scipy.ndimage.map_coordinates

        border_cval_seg: If border_mode_seg=constant, what value to use?

        order_seg: Order of interpolation for seg. see scipy.ndimage.map_coordinates. Strongly recommended to use 0!
        If !=0 then you will have to round to int and also beware of interpolation artifacts if you have more then
        labels 0 and 1. (for example if you have [0, 0, 0, 2, 2, 1, 0] the neighboring [0, 0, 2] bay result in [0, 1, 2])

        random_crop: True: do a random crop of size patch_size and minimal distance to border of
        patch_center_dist_from_border. False: do a center crop of size patch_size
    """
    def __init__(self, patch_size, patch_center_dist_from_border=30,
                 do_elastic_deform=True, alpha=(0., 1000.), sigma=(10., 13.),
                 do_rotation=True, angle_x=(0, 2 * np.pi), angle_y=(0, 2 * np.pi), angle_z=(0, 2 * np.pi),
                 do_scale=True, scale=(0.75, 1.25), border_mode_data='nearest', border_cval_data=0, order_data=3,
                 border_mode_seg='constant', border_cval_seg=0, order_seg=0, random_crop=True,
                 data_key="data", label_key="seg", p_el_per_sample=1, p_scale_per_sample=1, p_rot_per_sample=1):
        self.p_rot_per_sample = p_rot_per_sample
        self.p_scale_per_sample = p_scale_per_sample
        self.p_el_per_sample = p_el_per_sample
        self.data_key = data_key
        self.label_key = label_key
        self.patch_size = patch_size
        self.patch_center_dist_from_border = patch_center_dist_from_border
        self.do_elastic_deform = do_elastic_deform
        self.alpha = alpha
        self.sigma = sigma
        self.do_rotation = do_rotation
        self.angle_x = angle_x
        self.angle_y = angle_y
        self.angle_z = angle_z
        self.do_scale = do_scale
        self.scale = scale
        self.border_mode_data = border_mode_data
        self.border_cval_data = border_cval_data
        self.order_data = order_data
        self.border_mode_seg = border_mode_seg
        self.border_cval_seg = border_cval_seg
        self.order_seg = order_seg
        self.random_crop = random_crop

    def __call__(self, **data_dict):
        data = data_dict.get(self.data_key)
        seg = data_dict.get(self.label_key)

        if self.patch_size is None:
            if len(data.shape) == 4:
                patch_size = (data.shape[2], data.shape[3])
            elif len(data.shape) == 5:
                patch_size = (data.shape[2], data.shape[3], data.shape[4])
            else:
                raise ValueError("only support 2D/3D batch data.")
        else:
            patch_size = self.patch_size

        ret_val = augment_spatial(data, seg, patch_size=patch_size,
                                  patch_center_dist_from_border=self.patch_center_dist_from_border,
                                  do_elastic_deform=self.do_elastic_deform, alpha=self.alpha, sigma=self.sigma,
                                  do_rotation=self.do_rotation, angle_x=self.angle_x, angle_y=self.angle_y,
                                  angle_z=self.angle_z, do_scale=self.do_scale, scale=self.scale,
                                  border_mode_data=self.border_mode_data,
                                  border_cval_data=self.border_cval_data, order_data=self.order_data,
                                  border_mode_seg=self.border_mode_seg, border_cval_seg=self.border_cval_seg,
                                  order_seg=self.order_seg, random_crop=self.random_crop,
                                  p_el_per_sample=self.p_el_per_sample, p_scale_per_sample=self.p_scale_per_sample,
                                  p_rot_per_sample=self.p_rot_per_sample)

        data_dict[self.data_key] = ret_val[0]
        if seg is not None:
            data_dict[self.label_key] = ret_val[1]

        return data_dict

