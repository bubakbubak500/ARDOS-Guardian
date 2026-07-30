# Guardian 0.6.37

Three changes to *Settings → Radio control*, all from operating the 0.6.36
build: the PTT test told a VOX station something that was not true, it asked a
question nobody wanted, and the COM port still had to be typed from memory.

## VOX/serial PTT was reported as verified when it is not

The PTT test works with **VOX / serial PTT** — it opens the port and toggles
the RTS or DTR line exactly as a transmission would. What it could not do is
what 0.6.36 claimed: the serial backend's `get_state()` reads back the control
line *Guardian itself just asserted*, not anything the radio said. A dead
cable with a live COM port would still have reported "the radio reported TX".

Confirmation is now a property of the driver, not of the value it happens to
return. Hamlib asks the rig (`t`) and can confirm; VOX/serial echoes its own
wire and cannot. So a VOX station now gets:

> PTT test: the command was accepted and released after 2.0 s, but this
> backend cannot confirm TX — watch the radio itself.

The line-stuck check stays for both, because a PTT line left asserted is a
fault whoever is reporting it — the wording is now "PTT is still asserted
after unkeying" rather than blaming the radio.

## One click, no dialog

The confirmation popup is gone. The button keys, and that is that. The warning
it used to carry — a bare carrier on the current frequency, have an antenna or
dummy load connected — now lives under the button and in its tooltip, where it
can be read before clicking instead of after.

The "these settings were never applied" case no longer opens a dialog either;
it writes into the same status line as every other result.

## The COM port is a list again

*CAT / PTT serial port* was a bare text field: you had to remember whether the
radio came up as COM7 or COM11. It is now a dropdown of the ports that
actually exist, each with the adapter description Windows reports
(`COM7 — Silicon Labs CP210x`), with a **Refresh ports** button beside it.

It stays editable, because configuring a port for a radio that is unplugged
right now is perfectly reasonable — and only the bare device is saved, never
the description.

## Tests

The VOX read-back no longer counting as confirmation; the driver capability
flags themselves (Hamlib yes, VOX and none no); keying on the click with no
dialog of any kind appearing; the unapplied-settings refusal landing in the
status line; and the port picker — selecting by description, saving only the
device, and keeping a port that is not currently plugged in. Each was checked
against a deliberately broken implementation to confirm it fails there.
