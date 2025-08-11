# Underwater Vision Pipeline #

This repository is hosting the Underwater Vision Pipeline, which at the time of this writing consists of the combination of image-processing methods and colmap. The idea is to first process the images with a specified method and then running colmao to retrieve a 3D sparse reconstruction of the scene that was inspected.

## Installation

We use docker compose for this project. Make sure you follow their [installation guidelines](https://docs.docker.com/compose/install/).

After installing docker compose, run the following command

```bash
./clone_and_build.sh
```

This shell script will clone the repositories and build them with docker compose. It may take some time to install all the dependencies.

After the docker images are built, you can start using the pipeline.

## Usage

There are two configuration files on the [configs](./configs/) directory that you can use to test the pipeline.

Run the following command to run the pipeline with data coming from the simulation

```bash
sudo ./run_pipeline.sh --config ./configs/simulated_data.json
```

__Note__: Your personal user password may be required since `sudo` is run inside the shell script