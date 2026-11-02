# secrets/

Optional files for `docker-compose.secrets.yml` (the default route is `.env`; use one, not both).
Everything in this folder except this README is git-ignored and kept out of the Docker image.

| File | Holds |
| --- | --- |
| `maxmind_account_id` | your MaxMind account ID, for example `YOUR_ACCOUNT_ID` |
| `maxmind_license_key` | your MaxMind GeoLite2 licence key, for example `YOUR_LICENSE_KEY` |

Create them with mode 600, value only:

```bash
umask 077
printf '%s' 'YOUR_ACCOUNT_ID'  > secrets/maxmind_account_id
printf '%s' 'YOUR_LICENSE_KEY' > secrets/maxmind_license_key
```

MaxMind's container trims leading and trailing white space (including a trailing newline) from these
files, but `printf '%s'` keeps them exact anyway. The licence key is a secret like `GLOBALPING_TOKEN`:
never commit it, paste it into an issue or let it reach a log.
