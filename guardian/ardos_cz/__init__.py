"""Optional ARDOS CZ internet transport; RF remains independently usable."""

# Durable server custody blocks RF submission even without an attached client.
HELD_STATES = {'checking', 'uploading', 'unknown', 'accepted'}
