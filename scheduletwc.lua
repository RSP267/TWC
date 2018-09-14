-- dzVents example: schedule max TWC current
-- this example uses a schedule for setting the max charging current
-- you could use your solar power reading instead

return {
	active = true,

	-- triggers
	on = {
	    devices = {
            -- none
		},
		timer = {
			'every 1 minutes'
		}
	},

	execute = function(domoticz, device)

		-- Disable charging when I am at work
		local lowpowerschedule = 'at 08:00-24:00 on mon, tue, wed, thu, fri, sat, sun'
		local lowamps = 0
		local highamps = 99

		-- check schedule
		local maxamps = highamps
		if (domoticz.time.matchesRule(lowpowerschedule)) then
	    maxamps = lowamps
		end

		-- send setting to  twcmaster
		local twcDev = domoticz.devices('TWC - Total charge')
		if (twcDev) then
		  twcDev.setState(maxamps)
		  domoticz.log('Set Max current: '.. maxamps)
		end

	end
}
