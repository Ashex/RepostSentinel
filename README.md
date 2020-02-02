# RepostSentinel
Detects and moderates reposts on reddit

---

## Dependencies

The bot requires python 3.7+, library dependencies are in `requirements.txt`

## Getting Started


1) Specify configuration settings in `config.yml`, see `config.yml.example` for details.

2) Modify line 34 in `postgres/DBCreate.sql` with the name of your subreddit then bootstrap the database with it.

3) Run the bot with `python3 RepostSentinel.py`

## Running in docker

Build the container using the provided Dockerfile. Configuration is provided by setting environmental variables starting with RSENTINEL_ (e.g RSENTINEL_DB_HOST).

If you prefer you can also run postgres in docker by building it from the Dockerfile in `postgres` then linking the containers together. See the [postgres container registry](https://hub.docker.com/_/postgres/) for configuration details.
