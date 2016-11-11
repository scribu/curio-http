from setuptools import setup

setup(
    name='curio-http',
    version='0.1.0',
    description='Asynchronouse HTTP client based on curio.',
    classifiers=[
        'License :: OSI Approved :: Apache Software License',
        'Intended Audience :: Developers',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Development Status :: 2 - Pre-Alpha',
        'Operating System :: POSIX',
        'Operating System :: MacOS :: MacOS X',
        'Topic :: Internet :: WWW/HTTP'
    ],
    author='scribu',
    author_email='mail@scribu.net',
    url='https://github.com/scribu/curio-http/',
    license='Apache 2',
    packages=['curio_http'],
    install_requires=[
        'curio >=0.4',
        'h11 >=0.6',
        'yarl >=0.7',
    ],
    extras_require={
        'test': [
            'pytest >=3.0.3',
        ],
    },
)
