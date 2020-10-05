# -*- coding: utf-8 -*-

from trojanzoo.plot import *

import argparse
import numpy as np

import warnings
warnings.filterwarnings("ignore")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--dataset', dest='dataset', default='cifar10')
    parser.add_argument('-c', '--confidence', dest='confidence', action='store_true')
    args = parser.parse_args()
    name = 'figure1 %s alpha' % args.dataset
    if args.confidence:
        name += ' confidence'
    fig = Figure(name)
    fig.set_axis_label('x', 'Trigger Transparency')
    fig.set_axis_label('y', 'Misclassification Confidence' if args.confidence else 'Max Re-Mask Accuracy')
    fig.set_axis_lim('x', lim=[0, 1.0], piece=10, margin=[0.05, 0.05],
                     _format='%.1f')
    fig.set_axis_lim('y', lim=[0, 100], piece=5, margin=[0.0, 5.0],
                     _format='%d')
    fig.set_title(fig.name)

    color_list = [ting_color['red_carrot'], ting_color['red_deep'], ting_color['yellow'],
                  ting_color['blue'], ting_color['blue_light'], ting_color['pink'],
                  ting_color['green'], color['brown']['brown'], color['green']['army']]
    mark_list = ['.', ',', 'o', 'v', 's', 'p', '*', 'h', 'D']

    x = np.linspace(0.0, 1.0, 11)
    y = {
        'cifar10': {
            'badnet': [96.078, 96.078, 96.078, 96.078, 95.146, 94.118, 90.196, 83.810, 72.381, 52.577],
            'latent_backdoor': [100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 98.113],
            'trojannn': [100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 99.065, 97.196, 91.509, 62.617],
            'imc': [100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 100.000, 99.960, 99.220],
            'reflection_backdoor': [99.980, 99.810, 99.750, 99.430, 98.830, 97.330, 94.240, 87.400, 52.110, 10.660],
            'targeted_backdoor': [100.000, 100.000, 100.000, 100.000, 97.980, 95.146, 90.909, 79.412, 11.470, 10.680],
            'clean_label_pgd': [74.243, 51.167, 26.156, 13.037, 12.898, 12.712, 12.630, 12.661, 12.650, 10.540],
            'trojannet': [100, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352],
            'bypassing': [95.320, 95.250, 94.370, 93.880, 93.300, 92.070, 90.460, 88.790, 74.320, 49.270],
        },
        'gtsrb': {
            'badnet': [95.469, 96.875, 95.312, 93.75, 93.75, 90.476, 88.525, 82.540, 80.328, 63.934],
            'latent_backdoor': [100, 100, 100, 100, 100, 100, 100, 100, 100, 91.803],
            'trojannn': [99.029, 99.029, 98.701, 98.382, 97.411, 95.793, 92.557, 81.081, 76.882, 63.784],
            'imc': [100, 100, 99.981, 99.7, 98.93, 94.557, 87.763, 71.378, 0.619, 0.619],
            'reflection_backdoor': [94.989, 90.709, 91.16, 83.54, 76.952, 67.98, 58.408, 50.282, 40.935, 3.979],
            'targeted_backdoor': [85.366, 83.871, 77.419, 64.228, 56.911, 48.78, 39.837, 15.447, 13.821, 11.382],
            'clean_label_pgd': [59.553, 42.962, 5.912, 2.196, 1.52, 1.989, 1.314, 0.845, 0.938, 0.9760],
            # 'trojannet': [100, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352, 10.352],
        },
    }
    z = {
        # 'cifar10': {
        #     'badnet': [0.6471461653709412, 0.6486684679985046, 0.6302809119224548, 0.628552258014679, 0.5995506048202515, 0.5772883296012878, 0.5688976645469666, 0.5431302785873413, 0.5335214138031006, 0.5495538115501404],
        #     'latent_backdoor': [0.9638524651527405, 0.9766114354133606, 0.9740619659423828, 0.9637922644615173, 0.9497492909431458, 0.9425596594810486, 0.9369469285011292, 0.8903026580810547, 0.7312646508216858, 0.56],
        #     'trojannn': [0.7242116332054138, 0.6972057819366455, 0.6689168214797974, 0.6355840563774109, 0.605204701423645, 0.5644066333770752, 0.5464028716087341, 0.5420322418212891, 0.5582942962646484, 0.553290069103241],
        #     'imc': [0.9254073977470398, 0.827289879322052, 0.6991446614265442, 0.6506359577178955, 0.59810653924942017, 0.5479408502578735, 0.5525797605514526, 0.535953164100647, 0.45457735657691956, 0.4651917517185211],
        #     'clean_label_pgd': [0.9218209385871887, 0.8995527029037476, 0.8787757158279419, 0.8557542562484741, 0.8184587359428406, 0.7887511253356934, 0.785068929195404, 0.7558798789978027, 0.7620411515235901, 0.7771100997924805],
        # },
        # 'gtsrb': {
        #     'badnet': [53.153, 52.965, 52.046, 49.869, 46.539, 41.929, 37.782, 32.057, 28.679, 22.710, 3.247],
        #     'latent_backdoor': [92.080, 84.666, 84.234, 80.556, 78.491, 72.823, 63.382, 36.074, 0.976, 0.713, 0.713],
        #     'trojannn': [53.041, 46.059, 45.083, 42.361, 40.484, 38.514, 35.83, 31.119, 25.563, 18.975, 2.665],
        #     'targeted_backdoor': [40.897, 34.553, 31.794, 25.882, 19.651, 11.355, 6.55, 4.523, 3.397, 2.947, 2.721],
        # },
    }
    for key in z.keys():
        for sub_key in z[key].keys():
            for i in range(len(z[key][sub_key])):
                z[key][sub_key][i] *= 100
    # y = {
    #     'cifar10': {
    #         'badnet': [78.710, 76.910, 73.940, 67.310, 61.980, 54.450, 45.330, 37.290, 29.610, 22.960],
    #         'latent_backdoor': [97.160, 95.450, 90.080, 86.490, 82.010, 71.660, 55.440, 23.150, 10.190, 10.280],
    #         'trojannn': [75.360, 73.530, 71.450, 68.090, 64.060, 56.350, 48.030, 38.770, 30.910, 12.260],
    #     },
    #     'gtsrb': {
    #         'badnet': [56.757, 58.540, 56.813, 51.971, 44.613, 40.128, 39.02, 31.344, 28.378, 23.104, 3.247],
    #         'latent_backdoor': [92.080, 84.666, 84.234, 80.556, 78.491, 72.823, 63.382, 36.074, 0.976, 0.713, 0.713],
    #         'trojannn': [59.816, 58.84, 56.175, 53.416, 49.906, 43.431, 42.492, 35.098, 27.721, 23.292, 3.453],
    #         'targeted_backdoor': [40.165, 33.315, 30.950, 26.051, 27.44, 20.383, 16.967, 15.447, 2.928, 2.628, 2.477],
    #     },
    # }
    y = z if args.confidence else y
    for i, (key, value) in enumerate(y[args.dataset].items()):
        fig.curve(x[:len(value)], value, color=color_list[i], label=key)
        fig.scatter(x[:len(value)], value, color=color_list[i], marker=mark_list[i])
    fig.save('./result/')
