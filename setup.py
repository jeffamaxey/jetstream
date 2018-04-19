import os

from setuptools import setup, find_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name="jetstream",
    version="0.1.0a1",
    author="Ryan Richholt",
    author_email="ryan@tgen.org",
    url="https://github.com/tgen/jetstream",
    description="NGS analysis pipeline at TGen.",
    long_description=read('README.md'),
    long_description_content_type="text/markdown",
    keywords="ngs pipeline automation",
    packages=find_packages(),
    python_requires='>=3.5',
    install_requires=['networkx', 'ruamel.yaml', 'ulid-py',
                      'tempstore', 'requests', 'jinja2'],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Topic :: Utilities",
    ],
    include_package_data=True,
    package_data={'jetstream': ['etc/**', 'plugins/README']},

    # Eventually this will be replaced with cli entry-points but for now it
    # helps me prototype quickly
    scripts=['examples/' + script for script in os.listdir('examples/')],

    entry_points={
        'console_scripts': [
            'jetstream=jetstream.core.cli.main:main'
        ],
    }

)
