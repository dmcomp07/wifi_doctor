from setuptools import setup, find_packages

setup(
    name="wifi-doctor",
    version="1.0.0",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'wifi-doctor=wifi_doctor.__main__:main',
        ],
    },
)
