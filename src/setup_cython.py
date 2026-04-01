"""
Cython Build Setup Script.

Run: python setup_cython.py build_ext --inplace
"""

from setuptools import setup
from Cython.Build import cythonize
import numpy as np

setup(
    name="polym_parser",
    ext_modules=cythonize(
        "parser.pyx",
        compiler_directives={
            'language_level': '3',
            'boundscheck': False,
            'wraparound': False,
            'cdivision': True,
        }
    ),
    include_dirs=[np.get_include()],
    zip_safe=False,
)
