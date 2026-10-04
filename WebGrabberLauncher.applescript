on run
	set projectDir to POSIX path of (container of (path to me) as alias)
	set commandString to "cd \"" & projectDir & "\" && ./start_webgrabber.command"
	delay 0.2
	do shell script commandString
end run
