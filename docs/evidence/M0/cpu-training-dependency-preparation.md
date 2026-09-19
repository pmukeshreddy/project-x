# CPU training dependency preparation — metadata only

Torch 2.11.0 has a published `torch-2.11.0-cp313-cp313-macosx_11_0_arm64.whl` for CPython 3.13 on macOS 11+ arm64. Its Requires-Python is `>=3.10`. The wheel is 80,606,801 bytes, SHA-256 `1e6debd97ccd3205bbb37eb806a9d8219e1139d15419982c09e23ef7d4369d18`. [PyPI release metadata](https://pypi.org/pypi/torch/2.11.0/json) and the wheel's hash-verified `.metadata` sidecar establish this distribution identity. No wheel was downloaded. This is the macOS distribution, not a claim that the binary has no MPS support; later diagnostics must explicitly use CPU tensors/devices.

The smallest no-extras runtime closure under the selected existing pins is ten distributions: torch 2.11.0; filelock 4.0.1; typing-extensions 4.16.0; setuptools 80.10.2; sympy 1.14.0; networkx 3.6.1; jinja2 3.1.6; fsspec 2026.9.0; markupsafe 3.0.3; mpmath 1.3.0. Five pins reuse the project lock; four new transitive pins reuse the inspected frozen SkyRL lock. Thus the increment over the already populated authoring closure is torch plus setuptools, sympy, networkx and mpmath. This is a metadata-derived minimum for Torch, excluding the separately retained project/core/dev closure. Pure tensor/optimizer diagnostics do not require NumPy according to the declared dependency graph. CUDA/Triton requirements are Linux-marked and inactive on this Mac; no Torch extras are selected.

Proposed later setup: a separate CPython 3.13 macOS arm64 diagnostic environment with this exact ten-wheel closure, each wheel hash pinned, plus the separately hash-pinned feature-rl/core dependencies needed by the eventual diagnostic. Use a reviewed offline wheelhouse and wheel-only `--no-index --no-deps --require-hashes --only-binary=:all:` installation; do not resolve latest dependencies or alter the existing authoring venv. These are preparation instructions, not executed commands or shared configuration. The filename tag and metadata were checked against this host's interpreter tags; import/linker behavior, CPU tensor/autograd/optimizer/save/reload checks and package consistency still require later authorized execution.

## Linux training overlay inspection

The coordinator's candidate plan is frozen SkyRL `uv sync --frozen --extra fsdp --extra harbor`, followed only by a hash-pinned Pydantic 2.13.5 / pydantic-core 2.46.5 overlay and the built, hashed feature-rl wheel. Runtime must use `uv run --offline --no-sync` (the existing smoke also uses `--frozen`); a later sync can restore upstream Pydantic and invalidate the overlay. Capture the post-overlay full package inventory, exact feature-rl build revision/wheel hash, and run package-consistency/import gates in that isolated Linux CPython 3.12 environment before claiming compatibility. No unbounded resolution or current project downgrade is proposed.

Actual retained sources bind SkyRL `f5bc3b78dfddfb352870d5d7430cd226e5785838` and Harbor `3de07a0e01f3368921766437fc7afece3ddec23d`. Harbor declares `pydantic>=2.11.7`. SkyRL's frozen lock has Pydantic 2.13.4/core 2.46.4, annotated-types 0.7.0, typing-extensions 4.15.0 and typing-inspection 0.4.2. The project pins Pydantic 2.13.5/core 2.46.5; auxiliary constraints must be checked below before approving the two-package overlay. Source-level requirement compatibility does not verify the full CUDA/vLLM/FSDP/Harbor installation or execution. No M7 overlay report was present in `docs/evidence/M7` during this inspection; this candidate protocol comes from the coordinator dispatch and existing upstream source/lock, not an observed installation.

## Exact wheel and metadata receipt

The complete selected wheels, URLs, SHA-256, sizes, Requires-Python and Requires-Dist (including inactive extras) follow. Metadata marker evaluation confirmed every active dependency closes over the ten exact versions on this host; this was a read-only metadata calculation, not a package resolver, installation or test run.

```json
{
  "revision": "9c9adf08e9bf68a216ca6f9a3e05a319e445844d",
  "host_metadata_environment": {
    "implementation_name": "cpython",
    "implementation_version": "3.13.7",
    "os_name": "posix",
    "platform_machine": "arm64",
    "platform_release": "25.3.0",
    "platform_system": "Darwin",
    "platform_version": "Darwin Kernel Version 25.3.0: Wed Jan 28 20:49:24 PST 2026; root:xnu-12377.81.4~5/RELEASE_ARM64_T8132",
    "python_full_version": "3.13.7",
    "platform_python_implementation": "CPython",
    "python_version": "3.13",
    "sys_platform": "darwin",
    "extra": ""
  },
  "selected_wheel_bytes_total": 91095546,
  "wheels": [
    {
      "name": "torch",
      "version": "2.11.0",
      "filename": "torch-2.11.0-cp313-cp313-macosx_11_0_arm64.whl",
      "url": "https://files.pythonhosted.org/packages/87/89/5ea6722763acee56b045435fb84258db7375c48165ec8be7880ab2b281c5/torch-2.11.0-cp313-cp313-macosx_11_0_arm64.whl",
      "sha256": "1e6debd97ccd3205bbb37eb806a9d8219e1139d15419982c09e23ef7d4369d18",
      "size": 80606801,
      "requires_python": ">=3.10",
      "requires_dist": [
        "filelock",
        "typing-extensions>=4.10.0",
        "setuptools<82",
        "sympy>=1.13.3",
        "networkx>=2.5.1",
        "jinja2",
        "fsspec>=0.8.5",
        "cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == \"Linux\"",
        "cuda-bindings<14,>=13.0.3; platform_system == \"Linux\"",
        "nvidia-cudnn-cu13==9.19.0.56; platform_system == \"Linux\"",
        "nvidia-cusparselt-cu13==0.8.0; platform_system == \"Linux\"",
        "nvidia-nccl-cu13==2.28.9; platform_system == \"Linux\"",
        "nvidia-nvshmem-cu13==3.4.5; platform_system == \"Linux\"",
        "triton==3.6.0; platform_system == \"Linux\"",
        "optree>=0.13.0; extra == \"optree\"",
        "opt-einsum>=3.3; extra == \"opt-einsum\"",
        "pyyaml; extra == \"pyyaml\""
      ],
      "active_requires_dist": [
        "filelock",
        "typing-extensions>=4.10.0",
        "setuptools<82",
        "sympy>=1.13.3",
        "networkx>=2.5.1",
        "jinja2",
        "fsspec>=0.8.5"
      ],
      "metadata_sha256": "d65e0ab5a65ced0dce799a2f6f32bff57b3d3e9d050069f607ef71eb24dd9976",
      "selection": "requested exact version"
    },
    {
      "name": "filelock",
      "version": "4.0.1",
      "filename": "filelock-4.0.1-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/29/33/af0635ab07fe83b1788a1dbe370ff3e226062495a998335cb18a1cac81aa/filelock-4.0.1-py3-none-any.whl",
      "sha256": "481a321a27bef441e23c53371c6abc8d7d16e26b97090074ba44f7538a3fd55a",
      "size": 106219,
      "requires_python": ">=3.10",
      "requires_dist": [],
      "active_requires_dist": [],
      "metadata_sha256": "ce30fe72bf8baf43ed9754c9f37abe0b2b982eae6fd96e5fa8e0dc7e46fdee55",
      "selection": "current project pin"
    },
    {
      "name": "typing-extensions",
      "version": "4.16.0",
      "filename": "typing_extensions-4.16.0-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl",
      "sha256": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
      "size": 45571,
      "requires_python": ">=3.9",
      "requires_dist": [],
      "active_requires_dist": [],
      "metadata_sha256": "b05084ca1d50879865178d9fff9fabeab61bdfb1f361bfbde95421ffc8f9be46",
      "selection": "current project pin"
    },
    {
      "name": "jinja2",
      "version": "3.1.6",
      "filename": "jinja2-3.1.6-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/62/a1/3d680cbfd5f4b8f15abc1d571870c5fc3e594bb582bc3b64ea099db13e56/jinja2-3.1.6-py3-none-any.whl",
      "sha256": "85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67",
      "size": 134899,
      "requires_python": ">=3.7",
      "requires_dist": [
        "MarkupSafe>=2.0",
        "Babel>=2.7 ; extra == \"i18n\""
      ],
      "active_requires_dist": [
        "MarkupSafe>=2.0"
      ],
      "metadata_sha256": "68c5548fb67c4132a13898d9b31ec50c6bea2abdd915d921f214355c3a6499c8",
      "selection": "current project pin"
    },
    {
      "name": "fsspec",
      "version": "2026.9.0",
      "filename": "fsspec-2026.9.0-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/6c/c0/a98505f18594f1bce828bb159cec0fcf9860562f1a2c85913409fc8f3d9e/fsspec-2026.9.0-py3-none-any.whl",
      "sha256": "8dd6e646e99ea382bd85f97a45e6b526a442d79423a7dc673f1e2756d05fcb5f",
      "size": 221738,
      "requires_python": ">=3.10",
      "requires_dist": [
        "adlfs; extra == 'abfs'",
        "adlfs; extra == 'adl'",
        "pyarrow>=1; extra == 'arrow'",
        "dask; extra == 'dask'",
        "distributed; extra == 'dask'",
        "pre-commit; extra == 'dev'",
        "ruff>=0.5; extra == 'dev'",
        "numpydoc; extra == 'doc'",
        "sphinx; extra == 'doc'",
        "sphinx-design; extra == 'doc'",
        "sphinx-rtd-theme; extra == 'doc'",
        "yarl; extra == 'doc'",
        "dropbox; extra == 'dropbox'",
        "dropboxdrivefs; extra == 'dropbox'",
        "requests; extra == 'dropbox'",
        "adlfs; extra == 'full'",
        "aiohttp!=4.0.0a0,!=4.0.0a1; extra == 'full'",
        "dask; extra == 'full'",
        "distributed; extra == 'full'",
        "dropbox; extra == 'full'",
        "dropboxdrivefs; extra == 'full'",
        "fusepy; extra == 'full'",
        "gcsfs>=2026.4.0; extra == 'full'",
        "libarchive-c; extra == 'full'",
        "ocifs; extra == 'full'",
        "panel; extra == 'full'",
        "paramiko; extra == 'full'",
        "pyarrow>=1; extra == 'full'",
        "pygit2; extra == 'full'",
        "requests; extra == 'full'",
        "s3fs>=2026.6.0; extra == 'full'",
        "smbprotocol; extra == 'full'",
        "tqdm; extra == 'full'",
        "fusepy; extra == 'fuse'",
        "gcsfs>=2026.4.0; extra == 'gcs'",
        "pygit2; extra == 'git'",
        "requests; extra == 'github'",
        "gcsfs>=2026.4.0; extra == 'gs'",
        "panel; extra == 'gui'",
        "pyarrow>=1; extra == 'hdfs'",
        "aiohttp!=4.0.0a0,!=4.0.0a1; extra == 'http'",
        "libarchive-c; extra == 'libarchive'",
        "ocifs; extra == 'oci'",
        "s3fs>=2026.6.0; extra == 's3'",
        "paramiko; extra == 'sftp'",
        "smbprotocol; extra == 'smb'",
        "paramiko; extra == 'ssh'",
        "aiohttp!=4.0.0a0,!=4.0.0a1; extra == 'test'",
        "numpy; extra == 'test'",
        "pytest; extra == 'test'",
        "pytest-asyncio!=0.22.0; extra == 'test'",
        "pytest-benchmark; extra == 'test'",
        "pytest-cov; extra == 'test'",
        "pytest-mock; extra == 'test'",
        "pytest-recording; extra == 'test'",
        "pytest-rerunfailures; extra == 'test'",
        "requests; extra == 'test'",
        "aiobotocore<3.0.0,>=2.5.4; extra == 'test-downstream'",
        "dask[dataframe,test]; extra == 'test-downstream'",
        "moto[server]<5,>4; extra == 'test-downstream'",
        "pytest-timeout; extra == 'test-downstream'",
        "xarray; extra == 'test-downstream'",
        "zarr; extra == 'test-downstream'",
        "adlfs; extra == 'test-full'",
        "aiohttp!=4.0.0a0,!=4.0.0a1; extra == 'test-full'",
        "backports-zstd; (python_version < '3.14') and extra == 'test-full'",
        "cloudpickle; extra == 'test-full'",
        "dask; extra == 'test-full'",
        "distributed; extra == 'test-full'",
        "dropbox; extra == 'test-full'",
        "dropboxdrivefs; extra == 'test-full'",
        "fastparquet; extra == 'test-full'",
        "fusepy; extra == 'test-full'",
        "gcsfs>=2026.4.0; extra == 'test-full'",
        "jinja2; extra == 'test-full'",
        "kerchunk; extra == 'test-full'",
        "libarchive-c; extra == 'test-full'",
        "lz4; extra == 'test-full'",
        "notebook; extra == 'test-full'",
        "numpy; extra == 'test-full'",
        "ocifs; extra == 'test-full'",
        "pandas<3.0.0; extra == 'test-full'",
        "panel; extra == 'test-full'",
        "paramiko; extra == 'test-full'",
        "pyarrow>=1; extra == 'test-full'",
        "pyftpdlib; extra == 'test-full'",
        "pygit2; extra == 'test-full'",
        "pytest; extra == 'test-full'",
        "pytest-asyncio!=0.22.0; extra == 'test-full'",
        "pytest-benchmark; extra == 'test-full'",
        "pytest-cov; extra == 'test-full'",
        "pytest-mock; extra == 'test-full'",
        "pytest-recording; extra == 'test-full'",
        "pytest-rerunfailures; extra == 'test-full'",
        "python-snappy; extra == 'test-full'",
        "requests; extra == 'test-full'",
        "s3fs>=2026.6.0; extra == 'test-full'",
        "smbprotocol; extra == 'test-full'",
        "tqdm; extra == 'test-full'",
        "urllib3; extra == 'test-full'",
        "zarr<3.2.0; extra == 'test-full'",
        "zstandard; (python_version < '3.14') and extra == 'test-full'",
        "tqdm; extra == 'tqdm'"
      ],
      "active_requires_dist": [],
      "metadata_sha256": "68f5a262767510638e9b1933b1493f2baadc9616fe1e696f62aedd59c8c0a37c",
      "selection": "current project pin"
    },
    {
      "name": "markupsafe",
      "version": "3.0.3",
      "filename": "markupsafe-3.0.3-cp313-cp313-macosx_11_0_arm64.whl",
      "url": "https://files.pythonhosted.org/packages/9c/d9/5f7756922cdd676869eca1c4e3c0cd0df60ed30199ffd775e319089cb3ed/markupsafe-3.0.3-cp313-cp313-macosx_11_0_arm64.whl",
      "sha256": "116bb52f642a37c115f517494ea5feb03889e04df47eeff5b130b1808ce7c219",
      "size": 12029,
      "requires_python": ">=3.9",
      "requires_dist": [],
      "active_requires_dist": [],
      "metadata_sha256": "12b4cc61a7fa288cf7667ee3f213786d9619db57fb33ff6f934afbcb5c12ec81",
      "selection": "current project pin"
    },
    {
      "name": "setuptools",
      "version": "80.10.2",
      "filename": "setuptools-80.10.2-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/94/b8/f1f62a5e3c0ad2ff1d189590bfa4c46b4f3b6e49cef6f26c6ee4e575394d/setuptools-80.10.2-py3-none-any.whl",
      "sha256": "95b30ddfb717250edb492926c92b5221f7ef3fbcc2b07579bcd4a27da21d0173",
      "size": 1064234,
      "requires_python": ">=3.9",
      "requires_dist": [
        "pytest!=8.1.*,>=6; extra == \"test\"",
        "virtualenv>=13.0.0; extra == \"test\"",
        "wheel>=0.44.0; extra == \"test\"",
        "pip>=19.1; extra == \"test\"",
        "packaging>=24.2; extra == \"test\"",
        "jaraco.envs>=2.2; extra == \"test\"",
        "pytest-xdist>=3; extra == \"test\"",
        "jaraco.path>=3.7.2; extra == \"test\"",
        "build[virtualenv]>=1.0.3; extra == \"test\"",
        "filelock>=3.4.0; extra == \"test\"",
        "ini2toml[lite]>=0.14; extra == \"test\"",
        "tomli-w>=1.0.0; extra == \"test\"",
        "pytest-timeout; extra == \"test\"",
        "pytest-perf; sys_platform != \"cygwin\" and extra == \"test\"",
        "jaraco.develop>=7.21; (python_version >= \"3.9\" and sys_platform != \"cygwin\") and extra == \"test\"",
        "pytest-home>=0.5; extra == \"test\"",
        "pytest-subprocess; extra == \"test\"",
        "pyproject-hooks!=1.1; extra == \"test\"",
        "jaraco.test>=5.5; extra == \"test\"",
        "sphinx>=3.5; extra == \"doc\"",
        "jaraco.packaging>=9.3; extra == \"doc\"",
        "rst.linker>=1.9; extra == \"doc\"",
        "furo; extra == \"doc\"",
        "sphinx-lint; extra == \"doc\"",
        "jaraco.tidelift>=1.4; extra == \"doc\"",
        "pygments-github-lexers==0.0.5; extra == \"doc\"",
        "sphinx-favicon; extra == \"doc\"",
        "sphinx-inline-tabs; extra == \"doc\"",
        "sphinx-reredirects; extra == \"doc\"",
        "sphinxcontrib-towncrier; extra == \"doc\"",
        "sphinx-notfound-page<2,>=1; extra == \"doc\"",
        "pyproject-hooks!=1.1; extra == \"doc\"",
        "towncrier<24.7; extra == \"doc\"",
        "packaging>=24.2; extra == \"core\"",
        "more_itertools>=8.8; extra == \"core\"",
        "jaraco.text>=3.7; extra == \"core\"",
        "importlib_metadata>=6; python_version < \"3.10\" and extra == \"core\"",
        "tomli>=2.0.1; python_version < \"3.11\" and extra == \"core\"",
        "wheel>=0.43.0; extra == \"core\"",
        "platformdirs>=4.2.2; extra == \"core\"",
        "jaraco.functools>=4; extra == \"core\"",
        "more_itertools; extra == \"core\"",
        "pytest-checkdocs>=2.4; extra == \"check\"",
        "pytest-ruff>=0.2.1; sys_platform != \"cygwin\" and extra == \"check\"",
        "ruff>=0.8.0; sys_platform != \"cygwin\" and extra == \"check\"",
        "pytest-cov; extra == \"cover\"",
        "pytest-enabler>=2.2; extra == \"enabler\"",
        "pytest-mypy; extra == \"type\"",
        "mypy==1.14.*; extra == \"type\"",
        "importlib_metadata>=7.0.2; python_version < \"3.10\" and extra == \"type\"",
        "jaraco.develop>=7.21; sys_platform != \"cygwin\" and extra == \"type\""
      ],
      "active_requires_dist": [],
      "metadata_sha256": "dbcc9841388b7fb55c2190288fd683b183b0fb557f57a338d86553ef471d287d",
      "selection": "upstream frozen pin"
    },
    {
      "name": "sympy",
      "version": "1.14.0",
      "filename": "sympy-1.14.0-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/a2/09/77d55d46fd61b4a135c444fc97158ef34a095e5681d0a6c10b75bf356191/sympy-1.14.0-py3-none-any.whl",
      "sha256": "e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5",
      "size": 6299353,
      "requires_python": ">=3.9",
      "requires_dist": [
        "mpmath<1.4,>=1.1.0",
        "pytest>=7.1.0; extra == \"dev\"",
        "hypothesis>=6.70.0; extra == \"dev\""
      ],
      "active_requires_dist": [
        "mpmath<1.4,>=1.1.0"
      ],
      "metadata_sha256": "b756c2fbfd5be05ac5bdb0ebca61f55618f30f633ed92d88a5687429313a7595",
      "selection": "upstream frozen pin"
    },
    {
      "name": "networkx",
      "version": "3.6.1",
      "filename": "networkx-3.6.1-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/9e/c9/b2622292ea83fbb4ec318f5b9ab867d0a28ab43c5717bb85b0a5f6b3b0a4/networkx-3.6.1-py3-none-any.whl",
      "sha256": "d47fbf302e7d9cbbb9e2555a0d267983d2aa476bac30e90dfbe5669bd57f3762",
      "size": 2068504,
      "requires_python": "!=3.14.1,>=3.11",
      "requires_dist": [
        "asv; extra == \"benchmarking\"",
        "virtualenv; extra == \"benchmarking\"",
        "numpy>=1.25; extra == \"default\"",
        "scipy>=1.11.2; extra == \"default\"",
        "matplotlib>=3.8; extra == \"default\"",
        "pandas>=2.0; extra == \"default\"",
        "pre-commit>=4.1; extra == \"developer\"",
        "mypy>=1.15; extra == \"developer\"",
        "sphinx>=8.0; extra == \"doc\"",
        "pydata-sphinx-theme>=0.16; extra == \"doc\"",
        "sphinx-gallery>=0.18; extra == \"doc\"",
        "numpydoc>=1.8.0; extra == \"doc\"",
        "pillow>=10; extra == \"doc\"",
        "texext>=0.6.7; extra == \"doc\"",
        "myst-nb>=1.1; extra == \"doc\"",
        "intersphinx-registry; extra == \"doc\"",
        "osmnx>=2.0.0; extra == \"example\"",
        "momepy>=0.7.2; extra == \"example\"",
        "contextily>=1.6; extra == \"example\"",
        "seaborn>=0.13; extra == \"example\"",
        "cairocffi>=1.7; extra == \"example\"",
        "igraph>=0.11; extra == \"example\"",
        "scikit-learn>=1.5; extra == \"example\"",
        "iplotx>=0.9.0; extra == \"example\"",
        "lxml>=4.6; extra == \"extra\"",
        "pygraphviz>=1.14; extra == \"extra\"",
        "pydot>=3.0.1; extra == \"extra\"",
        "sympy>=1.10; extra == \"extra\"",
        "build>=0.10; extra == \"release\"",
        "twine>=4.0; extra == \"release\"",
        "wheel>=0.40; extra == \"release\"",
        "changelist==0.5; extra == \"release\"",
        "pytest>=7.2; extra == \"test\"",
        "pytest-cov>=4.0; extra == \"test\"",
        "pytest-xdist>=3.0; extra == \"test\"",
        "pytest-mpl; extra == \"test-extras\"",
        "pytest-randomly; extra == \"test-extras\""
      ],
      "active_requires_dist": [],
      "metadata_sha256": "aca5d94a97d1f70f301d033addb635f6e66be8973a00aba4127637eed2ef316a",
      "selection": "upstream frozen pin"
    },
    {
      "name": "mpmath",
      "version": "1.3.0",
      "filename": "mpmath-1.3.0-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/43/e3/7d92a15f894aa0c9c4b49b8ee9ac9850d6e63b03c9c32c0367a13ae62209/mpmath-1.3.0-py3-none-any.whl",
      "sha256": "a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c",
      "size": 536198,
      "requires_python": null,
      "requires_dist": [
        "pytest (>=4.6) ; extra == 'develop'",
        "pycodestyle ; extra == 'develop'",
        "pytest-cov ; extra == 'develop'",
        "codecov ; extra == 'develop'",
        "wheel ; extra == 'develop'",
        "sphinx ; extra == 'docs'",
        "gmpy2 (>=2.1.0a4) ; (platform_python_implementation != \"PyPy\") and extra == 'gmpy'",
        "pytest (>=4.6) ; extra == 'tests'"
      ],
      "active_requires_dist": [],
      "metadata_sha256": "44b66ea444b9c0d19ae94815d356bf047ae6b680c19268b5c265687cd6a81406",
      "selection": "upstream frozen pin"
    }
  ],
  "project_overlay_wheels": {
    "pydantic": [
      {
        "url": "https://files.pythonhosted.org/packages/eb/47/c95ffc2009878c7aac0c5e08528022dcb885933252a88b5f170058014464/pydantic-2.13.5-py3-none-any.whl",
        "hash": "sha256:346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73",
        "size": 472589,
        "upload-time": "2026-08-28T14:03:59.136Z"
      }
    ],
    "pydantic-core-cp312-linux-x86_64": [
      {
        "url": "https://files.pythonhosted.org/packages/c0/a4/eb9409ec0736e50aa70a412f16c204ed149516846912f7e6724d4c73ee53/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "hash": "sha256:0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2",
        "size": 2066284,
        "upload-time": "2026-08-28T09:58:32.289Z"
      }
    ]
  },
  "source_sha256": {
    "docs/decisions/code-completion-scope.md": "e2b917f58027ba7129264b30f1177b897cfef68e8a2ac1c0fe2d963423b6c20d",
    "pyproject.toml": "d682f26759ea0f7df4c64d89f86429e2c9febe199c6e2c51d6adad0e8cfd5e53",
    "uv.lock": "23aed10cf14c548ca6a9791b7674e504af986a7bc1d0ba0c7941d368b7e7148c",
    ".feature-rl/research/M7/skyrl/uv.lock": "5261a4eab76f558714ded054dfa66007e90bbd70b50069ac82d16a52fc81d3e9",
    ".feature-rl/research/M7/skyrl/pyproject.toml": "60c7d573298b2b286bf735f188a5ca1845caa8b68a8aa4e105bf6f50fce8d5df",
    ".feature-rl/research/M7/harbor/pyproject.toml": "dec2416332c75c2fe3d07328f25a41fa89b307753bf8c8c3e0bd955b60a6aee7",
    "docs/evidence/M7/source-inventory.jsonl": "42de7373b0a45ca61a1e37d997190d43bd98982e7619e4e4ce6b01446d8d00c5"
  },
  "network_response_bytes_including_initial_probes": 379611,
  "network_response_cap_bytes": 4194304,
  "initial_probes": [
    {
      "url": "https://pypi.org/pypi/torch/2.11.0/json",
      "bytes": 55087,
      "sha256": "9a5555ae018efa6cf41d48383acf535046e394eb4014f95e27483a458eaaaa9b",
      "utc": "2026-09-19T20:28:44.909925+00:00"
    },
    {
      "url": "https://files.pythonhosted.org/packages/87/89/5ea6722763acee56b045435fb84258db7375c48165ec8be7880ab2b281c5/torch-2.11.0-cp313-cp313-macosx_11_0_arm64.whl.metadata",
      "bytes": 29846,
      "sha256": "d65e0ab5a65ced0dce799a2f6f32bff57b3d3e9d050069f607ef71eb24dd9976"
    }
  ],
  "http_receipts": [
    {
      "url": "https://pypi.org/pypi/torch/2.11.0/json",
      "utc": "2026-09-19T20:30:39.972199+00:00",
      "bytes": 55087,
      "sha256": "9a5555ae018efa6cf41d48383acf535046e394eb4014f95e27483a458eaaaa9b",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/87/89/5ea6722763acee56b045435fb84258db7375c48165ec8be7880ab2b281c5/torch-2.11.0-cp313-cp313-macosx_11_0_arm64.whl.metadata",
      "utc": "2026-09-19T20:30:40.028765+00:00",
      "bytes": 29846,
      "sha256": "d65e0ab5a65ced0dce799a2f6f32bff57b3d3e9d050069f607ef71eb24dd9976",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/filelock/4.0.1/json",
      "utc": "2026-09-19T20:30:40.086244+00:00",
      "bytes": 4097,
      "sha256": "2b6f209353c75130f29507a98f6a5d1ffe213494704f5ca447bce097693b3ac0",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/29/33/af0635ab07fe83b1788a1dbe370ff3e226062495a998335cb18a1cac81aa/filelock-4.0.1-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.135374+00:00",
      "bytes": 2027,
      "sha256": "ce30fe72bf8baf43ed9754c9f37abe0b2b982eae6fd96e5fa8e0dc7e46fdee55",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/typing-extensions/4.16.0/json",
      "utc": "2026-09-19T20:30:40.192366+00:00",
      "bytes": 5631,
      "sha256": "2140da00d62dde967f52d0be0ae28444f1d6a3fe603f52c83c23988c32122d5a",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.245883+00:00",
      "bytes": 3310,
      "sha256": "b05084ca1d50879865178d9fff9fabeab61bdfb1f361bfbde95421ffc8f9be46",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/jinja2/3.1.6/json",
      "utc": "2026-09-19T20:30:40.300742+00:00",
      "bytes": 4925,
      "sha256": "1bc757d7065f3e67d3a49e72ebc731d0774055575732592eede9820c136329cc",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/62/a1/3d680cbfd5f4b8f15abc1d571870c5fc3e594bb582bc3b64ea099db13e56/jinja2-3.1.6-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.350172+00:00",
      "bytes": 2871,
      "sha256": "68c5548fb67c4132a13898d9b31ec50c6bea2abdd915d921f214355c3a6499c8",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/fsspec/2026.9.0/json",
      "utc": "2026-09-19T20:30:40.407160+00:00",
      "bytes": 11349,
      "sha256": "4a93a853911936a59262f3ccc1a36a42cfbff106beec53d6e247d00f7a3aa1e9",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/6c/c0/a98505f18594f1bce828bb159cec0fcf9860562f1a2c85913409fc8f3d9e/fsspec-2026.9.0-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.459985+00:00",
      "bytes": 10609,
      "sha256": "68f5a262767510638e9b1933b1493f2baadc9616fe1e696f62aedd59c8c0a37c",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/markupsafe/3.0.3/json",
      "utc": "2026-09-19T20:30:40.532855+00:00",
      "bytes": 80718,
      "sha256": "20faf559f1a150d84e7b0e7567b933fb3d383e6ba82179a1caa32a44cdc96085",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/9c/d9/5f7756922cdd676869eca1c4e3c0cd0df60ed30199ffd775e319089cb3ed/markupsafe-3.0.3-cp313-cp313-macosx_11_0_arm64.whl.metadata",
      "utc": "2026-09-19T20:30:40.583524+00:00",
      "bytes": 2690,
      "sha256": "12b4cc61a7fa288cf7667ee3f213786d9619db57fb33ff6f934afbcb5c12ec81",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/setuptools/80.10.2/json",
      "utc": "2026-09-19T20:30:40.640663+00:00",
      "bytes": 12469,
      "sha256": "9369a7d952eb4d60b1e1e925cea063a0d00226449f8aa346159d10314098042c",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/94/b8/f1f62a5e3c0ad2ff1d189590bfa4c46b4f3b6e49cef6f26c6ee4e575394d/setuptools-80.10.2-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.692222+00:00",
      "bytes": 6573,
      "sha256": "dbcc9841388b7fb55c2190288fd683b183b0fb557f57a338d86553ef471d287d",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/sympy/1.14.0/json",
      "utc": "2026-09-19T20:30:40.754889+00:00",
      "bytes": 15089,
      "sha256": "7f17c97b2d9ccec17969f30bd7e43c58db3de1145cd1cf00773e48991048d176",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/a2/09/77d55d46fd61b4a135c444fc97158ef34a095e5681d0a6c10b75bf356191/sympy-1.14.0-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.805563+00:00",
      "bytes": 12816,
      "sha256": "b756c2fbfd5be05ac5bdb0ebca61f55618f30f633ed92d88a5687429313a7595",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/networkx/3.6.1/json",
      "utc": "2026-09-19T20:30:40.869678+00:00",
      "bytes": 8458,
      "sha256": "e3e8cbca177240780bda0461c6ffaa9427d733907b620dc621318fca88d6fe31",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/9e/c9/b2622292ea83fbb4ec318f5b9ab867d0a28ab43c5717bb85b0a5f6b3b0a4/networkx-3.6.1-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:40.919521+00:00",
      "bytes": 6783,
      "sha256": "aca5d94a97d1f70f301d033addb635f6e66be8973a00aba4127637eed2ef316a",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/mpmath/1.3.0/json",
      "utc": "2026-09-19T20:30:40.976703+00:00",
      "bytes": 10700,
      "sha256": "87119e683fa33e0fcbca594469c138ecd42294fa5194f525c86787eefdfb77aa",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/43/e3/7d92a15f894aa0c9c4b49b8ee9ac9850d6e63b03c9c32c0367a13ae62209/mpmath-1.3.0-py3-none-any.whl.metadata",
      "utc": "2026-09-19T20:30:41.025628+00:00",
      "bytes": 8630,
      "sha256": "44b66ea444b9c0d19ae94815d356bf047ae6b680c19268b5c265687cd6a81406",
      "http_status": 200
    }
  ]
}
```

## Pydantic overlay metadata check and execution boundary

The active requirements below were checked against Linux CPython 3.12 and the retained upstream auxiliary pins. All are satisfied: only Pydantic and pydantic-core need replacing for this direct dependency closure. The CPython 3.12 Linux x86_64 core wheel SHA matches the project lock `0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2`. The future feature-rl wheel hash remains unavailable until the intended source revision is built; no placeholder is an installable pin. This local check covers Pydantic's own declared runtime dependencies and Harbor's lower bound, not every reverse constraint in the full optional training environment.

Executed commands were read-only `cat`, `rg`, `git rev-parse HEAD`, and `.venv/bin/python - <<'PY'` metadata scripts using stdlib `urllib.request`, `tomllib`, `email`, `hashlib`, and installed `packaging` for wheel-tag and requirement-marker inspection. HTTP requests were GETs only to the exact JSON/`.metadata` URLs captured here, each `urlopen(..., timeout=30)` and bounded `read(remaining)` with a shared 4 MiB total response allowance including initial probes. The main ten-wheel metadata command exited 0: `metadata_closure: all active requirements satisfied`, candidate wheel total 91,095,546 bytes; zero wheel bytes fetched. An initial shell glob `docs/reports/M7*` had no matches and was corrected to a literal directory search; it caused no writes. No product imports, installs, tests, models, environment edits, or GPU/CPU tensor operations were executed. Only this document was written.

Overlay result and final network accounting:

```json
{
  "upstream_auxiliary_pins": {
    "annotated-types": "0.7.0",
    "typing-extensions": "4.15.0",
    "typing-inspection": "0.4.2",
    "pydantic": "2.13.5",
    "pydantic-core": "2.46.5"
  },
  "overlay_metadata": [
    {
      "name": "pydantic",
      "version": "2.13.5",
      "filename": "pydantic-2.13.5-py3-none-any.whl",
      "url": "https://files.pythonhosted.org/packages/eb/47/c95ffc2009878c7aac0c5e08528022dcb885933252a88b5f170058014464/pydantic-2.13.5-py3-none-any.whl",
      "sha256": "346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73",
      "size": 472589,
      "requires_python": ">=3.9",
      "requires_dist": [
        "annotated-types>=0.6.0",
        "pydantic-core==2.46.5",
        "typing-extensions>=4.14.1",
        "typing-inspection>=0.4.2",
        "email-validator>=2.0.0; extra == 'email'",
        "tzdata; (python_version >= '3.9' and platform_system == 'Windows') and extra == 'timezone'"
      ],
      "active_requires_dist": [
        "annotated-types>=0.6.0",
        "pydantic-core==2.46.5",
        "typing-extensions>=4.14.1",
        "typing-inspection>=0.4.2"
      ]
    },
    {
      "name": "pydantic-core",
      "version": "2.46.5",
      "filename": "pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
      "url": "https://files.pythonhosted.org/packages/c0/a4/eb9409ec0736e50aa70a412f16c204ed149516846912f7e6724d4c73ee53/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
      "sha256": "0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2",
      "size": 2066284,
      "requires_python": ">=3.9",
      "requires_dist": [
        "typing-extensions>=4.14.1"
      ],
      "active_requires_dist": [
        "typing-extensions>=4.14.1"
      ]
    }
  ],
  "overlay_requirement_check": "all active direct dependencies satisfied",
  "additional_http_receipts": [
    {
      "url": "https://pypi.org/pypi/pydantic/2.13.5/json",
      "bytes": 113380,
      "sha256": "201fdebc5da596df2df23805e8f2b6386888fad4c396dadc48c1c297a61431a5",
      "utc": "2026-09-19T20:31:30.510247+00:00",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/eb/47/c95ffc2009878c7aac0c5e08528022dcb885933252a88b5f170058014464/pydantic-2.13.5-py3-none-any.whl.metadata",
      "bytes": 110178,
      "sha256": "0685830d13647bd6f3f05526043d32a8b2a221d86b53773053d6c52bb17a9c07",
      "utc": "2026-09-19T20:31:30.578365+00:00",
      "http_status": 200
    },
    {
      "url": "https://pypi.org/pypi/pydantic-core/2.46.5/json",
      "bytes": 113156,
      "sha256": "5b5b7b259b0b98f2594f92cb9255a1ce3ec978608a81def66193e77f65212a1c",
      "utc": "2026-09-19T20:31:30.652524+00:00",
      "http_status": 200
    },
    {
      "url": "https://files.pythonhosted.org/packages/c0/a4/eb9409ec0736e50aa70a412f16c204ed149516846912f7e6724d4c73ee53/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata",
      "bytes": 6573,
      "sha256": "29768c038c0bb3564c0a87ef50ef78d18a2372474bdef0ba356a8c0fd4bd6028",
      "utc": "2026-09-19T20:31:30.699961+00:00",
      "http_status": 200
    }
  ],
  "total_response_bytes_entire_task": 722898,
  "response_cap_bytes": 4194304,
  "command_exit_status": 0
}
```
