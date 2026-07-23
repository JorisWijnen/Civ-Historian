-- StatsDumper.lua
--
-- Logs an omniscient per-turn snapshot (players, civs, cities) via
-- Automation.Log(), which appends to Logs/Automation.log. This is a
-- read-only reporting mod: it never touches game state, only reads it.
--
-- Why Automation.Log() and not FireTuner: FireTuner requires an external
-- process to connect into a live game, which (a) is exactly the kind of
-- thing multiplayer anti-cheat correctly refuses to allow mid-session, and
-- (b) has been confirmed to crash Civ6 outright when anything beyond the
-- initial handshake is sent to a genuinely networked multiplayer session.
-- Automation.Log() runs entirely inside the game's own Lua VM, the same
-- way any other UI mod reads game state, so neither problem applies.
--
-- Field queries mirror scripts/dump_stats.py's LUA_PLAYERS_ROSTER /
-- LUA_CIVS_AND_CITIES, deliberately unconditional (no HasMet/fog gating)
-- so downstream tooling gets full data regardless of what any single human
-- player has explored or met.

local MARKER = "CIV6STATS_V4"

-- Terrain/feature/resource name tables never change during a game, but
-- dump them every turn anyway rather than gating on a "logged once" flag —
-- tried that (a Lua local set on first fire) and it silently breaks the
-- moment anything external truncates/rotates Automation.log, since the
-- flag lives in memory and has no idea the on-disk data it's guarding
-- disappeared. ~17 tiny lines/turn is a non-issue next to the full tile
-- grid dumped every turn regardless.
local function DumpMapLookup()
	local lines = {};
	local turn = Game.GetCurrentGameTurn();
	for row in GameInfo.Terrains() do
		table.insert(lines, MARKER.."|MAPLOOKUP_TERRAIN|"..turn.."|"..row.Index.."|"..row.TerrainType);
	end
	for row in GameInfo.Features() do
		table.insert(lines, MARKER.."|MAPLOOKUP_FEATURE|"..turn.."|"..row.Index.."|"..row.FeatureType);
	end
	for row in GameInfo.Resources() do
		table.insert(lines, MARKER.."|MAPLOOKUP_RESOURCE|"..turn.."|"..row.Index.."|"..row.ResourceType.."|"..(row.ResourceClassType or ""));
	end
	Automation.Log(table.concat(lines, "\n"));
end

-- Full tile grid (terrain/owner/hills/mountain/water/river/coastal/feature/
-- resource per tile), unconditional/omniscient like everything else here —
-- this is what lets external tooling render the same no-fog map image
-- scripts/dump_stats.py's --map-image already produces from a live
-- FireTuner query, just sourced from the mod's own log instead.
local function DumpMapTiles()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	local w, h = Map.GetGridSize();
	table.insert(lines, MARKER.."|GRIDSIZE|"..turn.."|"..w.."|"..h);
	for y = 0, h - 1 do
		local parts = {};
		for x = 0, w - 1 do
			local plot = Map.GetPlot(x, y);
			if plot then
				local terrainIdx = plot:GetTerrainType();
				local ownerIdx = plot:GetOwner();
				local hills = plot:IsHills() and 1 or 0;
				local mtn = plot:IsMountain() and 1 or 0;
				local water = plot:IsWater() and 1 or 0;
				local river = plot:IsRiver() and 1 or 0;
				local coastal = plot:IsCoastalLand() and 1 or 0;
				local featureIdx = plot:GetFeatureType();
				local resourceIdx = plot:GetResourceType();
				table.insert(parts, terrainIdx..","..ownerIdx..","..hills..","..mtn..","..water..","..river..","..coastal..","..featureIdx..","..resourceIdx);
			end
		end
		table.insert(lines, MARKER.."|ROW|"..turn.."|"..y.."|"..table.concat(parts, ";"));
	end
	Automation.Log(table.concat(lines, "\n"));
end

-- City yield indices 0-5 are the stable base-game order (Food, Production,
-- Gold, Science, Culture, Faith) -- same indices civ6-mcp's own city query
-- (src/civ_mcp/lua/cities.py) already relies on, not something that needs
-- a dynamic GameInfo.Yields lookup.
local function CityYieldsStr(c)
	local food, prod, gold, sci, cul, faith = 0, 0, 0, 0, 0, 0;
	pcall(function() food = c:GetYield(0); end);
	pcall(function() prod = c:GetYield(1); end);
	pcall(function() gold = c:GetYield(2); end);
	pcall(function() sci = c:GetYield(3); end);
	pcall(function() cul = c:GetYield(4); end);
	pcall(function() faith = c:GetYield(5); end);
	return "food="..string.format("%.2f", food).."|prod="..string.format("%.2f", prod)..
		"|gold="..string.format("%.2f", gold).."|sci="..string.format("%.2f", sci)..
		"|cul="..string.format("%.2f", cul).."|faith="..string.format("%.2f", faith);
end

-- Packed color -> "#RRGGBB". UI.GetPlayerColors() (confirmed via Firaxis'
-- own shipped Civ6Common.lua, used there for the colorblind-accessibility
-- "DifferentiateCivs" feature) returns colors packed as 0xAABBGGRR --
-- confirmed from that same file's DarkenLightenColor(), which pulls the
-- blue/green/red bytes out of a hex-formatted dump of the value in that
-- byte order. Red is therefore the LEAST significant byte here.
local function PackedColorToHex(c)
	local r = c % 256;
	local g = math.floor(c / 256) % 256;
	local b = math.floor(c / 65536) % 256;
	return string.format("#%02X%02X%02X", r, g, b);
end

-- A civ's real in-game primary/secondary colors (the ones its banners,
-- unit flags, etc. use). Civ6's own lobby already refuses to start a game
-- with two players assigned conflicting colors, so these are safe to use
-- directly downstream (render_map_lib.py) without collision-checking
-- against every other civ in the same game.
local function PlayerColorHex(i)
	local primary, secondary = "#999999", "#333333";
	pcall(function()
		local p, s = UI.GetPlayerColors(i);
		if p then primary = PackedColorToHex(p); end
		if s then secondary = PackedColorToHex(s); end
	end);
	return primary, secondary;
end

-- Majority religion of a single city, "none" if no religion has spread
-- there yet -- same GetMajorityReligion() call civ6-mcp's religion.py
-- already uses successfully per-city.
local function CityReligionName(c)
	local relName = "none";
	pcall(function()
		local majRel = c:GetReligion():GetMajorityReligion();
		if majRel >= 0 then
			local rRow = GameInfo.Religions[majRel];
			if rRow then relName = rRow.ReligionType; end
		end
	end);
	return relName;
end

-- A city-state's "type" (Culture/Science/Trade/Religious/Militaristic/
-- Industrial -- the trait that determines its envoy bonus) isn't exposed
-- as a direct property of the player; the base game itself derives it from
-- the city-state's LEADER, either directly or via that leader's
-- GameInfo.Leaders.InheritFrom -- confirmed from Firaxis' own shipped
-- PartialScreens/CityStates.lua (GetBonusText()), not a guess.
local MINOR_CIV_TYPE_LEADERS = {
	LEADER_MINOR_CIV_SCIENTIFIC = "SCIENCE",
	LEADER_MINOR_CIV_RELIGIOUS = "RELIGIOUS",
	LEADER_MINOR_CIV_TRADE = "COMMERCIAL",
	LEADER_MINOR_CIV_CULTURAL = "CULTURE",
	LEADER_MINOR_CIV_MILITARISTIC = "MILITARY",
	LEADER_MINOR_CIV_INDUSTRIAL = "INDUSTRIAL",
};

local function MinorCivType(i)
	local cstype = "UNKNOWN";
	pcall(function()
		local leader = PlayerConfigurations[i]:GetLeaderTypeName();
		if MINOR_CIV_TYPE_LEADERS[leader] then
			cstype = MINOR_CIV_TYPE_LEADERS[leader];
			return;
		end
		local info = GameInfo.Leaders[leader];
		if info and info.InheritFrom and MINOR_CIV_TYPE_LEADERS[info.InheritFrom] then
			cstype = MINOR_CIV_TYPE_LEADERS[info.InheritFrom];
		end
	end);
	return cstype;
end

local function DumpTurnStats()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	table.insert(lines, MARKER.."|TURN|"..turn);

	-- Full player roster (majors, minors/city-states, barbarians)
	for i = 0, 63 do
		local p = Players[i];
		if p ~= nil and p:IsAlive() then
			local cfg = PlayerConfigurations[i];
			local name = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?";
			local kind = "MINOR";
			if p:IsMajor() then kind = "MAJOR"; elseif p:IsBarbarian() then kind = "BARB"; end
			local cstype = (kind == "MINOR") and MinorCivType(i) or "";
			table.insert(lines, MARKER.."|PLAYER|"..turn.."|"..i.."|"..kind.."|"..name.."|cstype="..cstype);
		end
	end

	-- Per-major-civ stats + cities
	for i = 0, 62 do
		if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
			local cfg = PlayerConfigurations[i];
			local name = Locale.Lookup(cfg:GetCivilizationShortDescription());
			local leader = Locale.Lookup(cfg:GetLeaderName());
			local isHuman = false;
			pcall(function() isHuman = cfg:IsHuman(); end);
			local p = Players[i];
			local score = p:GetScore();
			local st = p:GetStats();
			local mil, techs, civics = 0, 0, 0;
			pcall(function() mil = st:GetMilitaryStrength(); end);
			pcall(function() techs = st:GetNumTechsResearched(); end);
			pcall(function() civics = st:GetNumCivicsCompleted(); end);
			local gold, goldYield, sciYield, culYield = 0, 0, 0, 0;
			pcall(function() gold = p:GetTreasury():GetGoldBalance(); end);
			pcall(function() goldYield = p:GetTreasury():GetGoldYield(); end);
			pcall(function() sciYield = p:GetTechs():GetScienceYield(); end);
			pcall(function() culYield = p:GetCulture():GetCultureYield(); end);
			local govName = "NONE";
			pcall(function()
				local govIdx = p:GetCulture():GetCurrentGovernment();
				if govIdx and govIdx >= 0 then
					local row = GameInfo.Governments[govIdx];
					if row then govName = row.GovernmentType; end
				end
			end);

			-- Full war graph for civ i, checked unconditionally against every
			-- other living major (not just relative to whichever player's
			-- client this happens to run on).
			local warWith = {};
			pcall(function()
				local diplo = p:GetDiplomacy();
				for k = 0, 62 do
					if k ~= i and Players[k] and Players[k]:IsAlive() and Players[k]:IsMajor() then
						local ok, atWarWithK = pcall(function() return diplo:IsAtWarWith(k); end);
						if ok and atWarWithK then table.insert(warWith, k); end
					end
				end
			end);

			-- Denunciations are directional: GetDiplomaticStateIndex(i) is k's
			-- stance toward i, which can differ from i's stance toward k.
			local diploStates = {"ALLIED","DECLARED_FRIEND","FRIENDLY","NEUTRAL","UNFRIENDLY","DENOUNCED","WAR"};
			local denouncedBy = {};
			pcall(function()
				for k = 0, 62 do
					if k ~= i and Players[k] and Players[k]:IsAlive() and Players[k]:IsMajor() then
						local ok, stateIdx = pcall(function() return Players[k]:GetDiplomaticAI():GetDiplomaticStateIndex(i); end);
						if ok and diploStates[stateIdx + 1] == "DENOUNCED" then table.insert(denouncedBy, k); end
					end
				end
			end);

			local primaryColor, secondaryColor = PlayerColorHex(i);

			table.insert(lines, MARKER.."|CIV|"..turn.."|"..i.."|"..name.."|"..leader..
				"|human="..tostring(isHuman)..
				"|score="..score.."|gold="..gold.."|goldpt="..goldYield.."|scipt="..sciYield..
				"|culpt="..culYield.."|mil="..mil.."|techs="..techs.."|civics="..civics..
				"|gov="..govName.."|atwarids="..table.concat(warWith, ",")..
				"|denouncedby="..table.concat(denouncedBy, ",")..
				"|primary="..primaryColor.."|secondary="..secondaryColor);

			local nCities, totalPop = 0, 0;
			for _, c in p:GetCities():Members() do
				nCities = nCities + 1;
				local pop = c:GetPopulation();
				totalPop = totalPop + pop;
				local cap = 0;
				pcall(function() cap = c:IsCapital() and 1 or 0; end);
				table.insert(lines, MARKER.."|CITY|"..turn.."|"..i.."|"..Locale.Lookup(c:GetName())..
					"|"..c:GetX()..","..c:GetY().."|pop="..pop.."|cap="..cap.."|"..CityYieldsStr(c)..
					"|rel="..CityReligionName(c));
			end
			table.insert(lines, MARKER.."|CIVTOTALS|"..turn.."|"..i.."|cities="..nCities.."|pop="..totalPop);
		end
	end

	-- City-states / Free Cities also own named cities, tracked separately
	-- since they don't carry the full CIV stat block above.
	for i = 0, 62 do
		if Players[i] and not Players[i]:IsMajor() and not Players[i]:IsBarbarian() and Players[i]:IsAlive() then
			pcall(function()
				for _, c in Players[i]:GetCities():Members() do
					local pop = c:GetPopulation();
					local cap = c:IsCapital() and 1 or 0;
					table.insert(lines, MARKER.."|MINORCITY|"..turn.."|"..i.."|"..Locale.Lookup(c:GetName())..
						"|"..c:GetX()..","..c:GetY().."|pop="..pop.."|cap="..cap.."|"..CityYieldsStr(c)..
						"|rel="..CityReligionName(c));
				end
			end);
		end
	end

	table.insert(lines, MARKER.."|END|"..turn);

	-- One Automation.Log() call per turn (confirmed 2026-07-17: a single
	-- call with embedded "\n" writes multiple distinct lines to
	-- Automation.log correctly), rather than one call per record.
	Automation.Log(table.concat(lines, "\n"));
end

-- Second, separate marker for unit-level operations/status. Written to the
-- same Logs/Automation.log as everything above -- Automation.Log() can only
-- ever target that one file, and there's no other Lua-exposed API anywhere
-- in the base game for opening/writing an arbitrary new log file (checked:
-- grepped the entire game's shipped Lua source for any alternative). The
-- "AutomationUnitOperations.log" this was requested as gets materialized on
-- the parsing side instead -- scripts/parse_mod_log.py splits these
-- CIV6UNITOPS_V2 lines out into their own file per session, since the mod
-- itself has no way to write a second physical file.
local UNITOPS_MARKER = "CIV6UNITOPS_V2"

local function UnitTypeName(unit)
	local ok, name = pcall(function()
		local row = GameInfo.Units[unit:GetUnitType()];
		return row and row.UnitType or "UNKNOWN";
	end);
	return (ok and name) or "UNKNOWN";
end

-- The native (engine) UnitOperations.log only logs a unit the turn a NEW
-- operation is queued for it -- a unit that's still fortified, healing, or
-- garrisoned from a previous turn goes completely silent every turn after
-- that, so army size/composition can't actually be read off it. This dumps
-- every living unit's current status every turn regardless of whether
-- anything about it changed, so idle/fortified/healing/garrisoned units
-- show up just as reliably as ones with a fresh order.
local function DumpUnitStatus()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	for i = 0, 63 do
		local p = Players[i];
		if p ~= nil and p:IsAlive() then
			-- No direct "is this unit garrisoned" unit method exists, so
			-- it's derived from position instead: a military unit sitting
			-- on the same tile as one of its own civ's cities.
			local ownCityPlots = {};
			pcall(function()
				for _, c in p:GetCities():Members() do
					ownCityPlots[c:GetX()..","..c:GetY()] = true;
				end
			end);

			local units = p:GetUnits();
			if units then
				for _, unit in units:Members() do
					pcall(function()
						local x, y = unit:GetX(), unit:GetY();
						local movesRemaining = unit:GetMovesRemaining();
						local fortifyTurns = 0;
						pcall(function() fortifyTurns = unit:GetFortifyTurns(); end);

						-- Same ActivityTypes classification Civ6's own unit
						-- panel/world tracker use (steamassets/base/assets/
						-- ui/panels/unitpanel.lua, worldtracker.lua) -- not
						-- a top-level table, since referencing ActivityTypes
						-- before the game's fully loaded could error out at
						-- file-load time and take the whole mod down with it.
						local activity = "AWAKE";
						pcall(function()
							local a = UnitManager.GetActivityType(unit);
							if a == ActivityTypes.ACTIVITY_SLEEP then activity = "SLEEP";
							elseif a == ActivityTypes.ACTIVITY_HOLD then activity = "FORTIFY";
							elseif a == ActivityTypes.ACTIVITY_SENTRY then activity = "SENTRY";
							elseif a == ActivityTypes.ACTIVITY_INTERCEPT then activity = "INTERCEPT";
							elseif a == ActivityTypes.ACTIVITY_OPERATION then activity = "OPERATION";
							elseif a == ActivityTypes.ACTIVITY_AWAKE then activity = "AWAKE";
							else activity = "OTHER"; end
						end);

						local garrisoned = ownCityPlots[x..","..y] and 1 or 0;
						table.insert(lines, UNITOPS_MARKER.."|UNIT|"..turn.."|"..i.."|"..unit:GetID()..
							"|"..UnitTypeName(unit).."|"..x..","..y..
							"|moves="..tostring(movesRemaining)..
							"|fortifyturns="..tostring(fortifyTurns)..
							"|activity="..activity..
							"|garrisoned="..garrisoned);
					end);
				end
			end
		end
	end
	table.insert(lines, UNITOPS_MARKER.."|UNITEND|"..turn);
	Automation.Log(table.concat(lines, "\n"));
end

-- The native UnitOperations.log records that a RANGE_ATTACK/etc operation
-- was queued, but never what it's actually attacking -- barbarians, a rival
-- civ's unit, or a city/district. GameEvents.OnCombatOccurred fires with
-- both sides' player/unit ids for every individual combat resolution, which
-- is enough to resolve the real target. Logged immediately rather than
-- batched at TurnBegin, since combat can happen at any point while a turn
-- is being processed, not only at its start.
local function DescribeCombatant(playerID, unitID, districtID)
	if playerID == nil or playerID < 0 then
		return "NONE";
	end
	if unitID ~= nil and unitID >= 0 then
		local ok, result = pcall(function()
			local p = Players[playerID];
			local unit = p and p:GetUnits():FindID(unitID);
			return unit and UnitTypeName(unit) or nil;
		end);
		if ok and result then return result; end
	end
	if districtID ~= nil and districtID >= 0 then
		return "CITY_OR_DISTRICT";
	end
	return "UNKNOWN";
end

local function OnCombatOccurred(attackerPlayerID, attackerUnitID, defenderPlayerID, defenderUnitID, attackerDistrictID, defenderDistrictID)
	pcall(function()
		local turn = Game.GetCurrentGameTurn();
		local attackerIsBarb = 0;
		pcall(function() attackerIsBarb = (Players[attackerPlayerID] and Players[attackerPlayerID]:IsBarbarian()) and 1 or 0; end);
		local defenderIsBarb = 0;
		pcall(function() defenderIsBarb = (Players[defenderPlayerID] and Players[defenderPlayerID]:IsBarbarian()) and 1 or 0; end);

		local line = UNITOPS_MARKER.."|COMBAT|"..turn..
			"|attacker="..tostring(attackerPlayerID).."|attackertype="..DescribeCombatant(attackerPlayerID, attackerUnitID, attackerDistrictID).."|attackerbarb="..attackerIsBarb..
			"|defender="..tostring(defenderPlayerID).."|defendertype="..DescribeCombatant(defenderPlayerID, defenderUnitID, defenderDistrictID).."|defenderbarb="..defenderIsBarb;
		Automation.Log(line);
	end);
end

-- Era, victory-condition, and per-civ religion state -- all confirmed live
-- via civ6-mcp's existing FireTuner queries (Game.GetEras(), GetStats()
-- victory-point accessors, Player:GetReligion()) in src/civ_mcp/lua/
-- overview.py, victory.py, religion.py -- ported here unconditionally
-- (every alive major, not gated on HasMet/local player) the same way
-- everything else in this file already is.
local function DumpDemographics()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	local eraManager = Game.GetEras();
	local eraIdx = eraManager:GetCurrentEra();
	local eraEntry = GameInfo.Eras[eraIdx];
	local eraType = eraEntry and eraEntry.EraType or "UNKNOWN";
	table.insert(lines, MARKER.."|ERA|"..turn.."|"..eraType.."|"..eraIdx);

	-- Which victory types this ruleset has enabled at all (same
	-- GameInfo.Victories + Game.IsVictoryEnabled check civ6-mcp's
	-- _LUA_VICTORY_ENABLED helper uses).
	local vtypes = {"VICTORY_TECHNOLOGY","VICTORY_CULTURE","VICTORY_RELIGIOUS","VICTORY_DIPLOMATIC","VICTORY_CONQUEST"};
	for _, vt in ipairs(vtypes) do
		local row = GameInfo.Victories[vt];
		if row then
			local ok, enabled = pcall(function() return Game.IsVictoryEnabled(row.Index); end);
			if ok and enabled then table.insert(lines, MARKER.."|VICTORYENABLED|"..turn.."|"..vt); end
		end
	end

	for i = 0, 62 do
		if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
			local p = Players[i];
			local age = "NORMAL";
			pcall(function()
				if eraManager:HasHeroicAge(i) then age = "HEROIC";
				elseif eraManager:HasGoldenAge(i) then age = "GOLDEN";
				elseif eraManager:HasDarkAge(i) then age = "DARK"; end
			end);
			local eraScore = 0;
			pcall(function() eraScore = eraManager:GetPlayerCurrentScore(i); end);
			local st = p:GetStats();
			local sciVP, sciNeeded, diploVP, tourism, relCities = 0, 0, 0, 0, 0;
			pcall(function() sciVP = st:GetScienceVictoryPoints(); end);
			pcall(function() sciNeeded = st:GetScienceVictoryPointsTotalNeeded(); end);
			pcall(function() diploVP = st:GetDiplomaticVictoryPoints(); end);
			pcall(function() tourism = st:GetTourism(); end);
			pcall(function() relCities = st:GetNumCitiesFollowingReligion(); end);
			local spaceports = 0;
			pcall(function()
				for _, c in p:GetCities():Members() do
					for _, d in c:GetDistricts():Members() do
						local dInfo = GameInfo.Districts[d:GetType()];
						if dInfo and dInfo.DistrictType == "DISTRICT_SPACEPORT" and d:IsComplete() then
							spaceports = spaceports + 1;
						end
					end
				end
			end);
			table.insert(lines, MARKER.."|CIVDEMO|"..turn.."|"..i..
				"|erascore="..eraScore.."|age="..age..
				"|scivp="..sciVP.."|scineeded="..sciNeeded..
				"|diplovp="..diploVP.."|tourism="..tourism..
				"|relcities="..relCities.."|spaceports="..spaceports);
		end
	end
	Automation.Log(table.concat(lines, "\n"));
end

local function DumpReligion()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	for i = 0, 62 do
		if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
			local rel = Players[i]:GetReligion();
			local created = "NONE";
			pcall(function()
				local relType = rel:GetReligionTypeCreated();
				if relType >= 0 then
					local rRow = GameInfo.Religions[relType];
					if rRow then created = rRow.ReligionType; end
				end
			end);
			local pantheon = "NONE";
			pcall(function()
				local panIdx = rel:GetPantheon();
				if panIdx >= 0 then
					local bRow = GameInfo.Beliefs[panIdx];
					if bRow then pantheon = bRow.BeliefType; end
				end
			end);
			local majority = "NONE";
			pcall(function()
				local majIdx = rel:GetReligionInMajorityOfCities();
				if majIdx >= 0 then
					local rRow = GameInfo.Religions[majIdx];
					if rRow then majority = rRow.ReligionType; end
				end
			end);
			local faith = 0;
			pcall(function() faith = rel:GetFaithBalance(); end);
			table.insert(lines, MARKER.."|CIVRELIGION|"..turn.."|"..i..
				"|created="..created.."|pantheon="..pantheon..
				"|majority="..majority.."|faith="..string.format("%.1f", faith));
		end
	end
	Automation.Log(table.concat(lines, "\n"));
end

-- Weather/disaster and Historic Moment tracking, both read off each
-- player's NotificationManager queue -- the same GetList/Find/GetTypeName/
-- GetMessage/GetAddedTurn/GetLocation calls civ6-mcp's own
-- src/civ_mcp/lua/notifications.py already uses successfully against the
-- local player -- looped over every alive player rather than just
-- Game.GetLocalPlayer().
--
-- UNVERIFIED IN MULTIPLAYER: NotificationManager has only ever been proven
-- (in this codebase) to work for the querying client's own player
-- (me = Game.GetLocalPlayer()). Whether querying another human player's ID
-- here actually returns their real notifications (vs. nil/empty) in a
-- genuine networked MP session has NOT been live-tested against a real
-- Automation.log yet -- do that before trusting opponent coverage here.
-- The exact "moment recorded" notification type name
-- (NOTIFICATION_PRIDE_MOMENT_RECORDED) and the disaster keyword list below
-- are likewise best-effort from public modding references, not yet
-- confirmed against a live log.
local EVENTS_MARKER = "CIV6EVENTS_V2";
local DISASTER_KEYWORDS = {
	"DISASTER", "STORM", "FLOOD", "VOLCAN", "DROUGHT", "TORNADO",
	"BLIZZARD", "HURRICANE", "TSUNAMI", "DUST", "WILDFIRE", "FOREST_FIRE",
};

local function IsDisasterType(typeName)
	for _, kw in ipairs(DISASTER_KEYWORDS) do
		if typeName:find(kw) then return true; end
	end
	return false;
end

local function DumpNotableEvents()
	local turn = Game.GetCurrentGameTurn();
	local lines = {};
	for i = 0, 63 do
		local p = Players[i];
		if p ~= nil and p:IsAlive() then
			pcall(function()
				local list = NotificationManager.GetList(i);
				if list then
					for _, nid in ipairs(list) do
						pcall(function()
							local entry = NotificationManager.Find(i, nid);
							if entry then
								local typeName = entry:GetTypeName() or "UNKNOWN";
								local msg = (entry:GetMessage() or ""):gsub("|", "/");
								if typeName == "NOTIFICATION_PRIDE_MOMENT_RECORDED" or typeName:find("MOMENT") then
									table.insert(lines, EVENTS_MARKER.."|MOMENT|"..turn.."|"..i.."|"..msg);
								elseif IsDisasterType(typeName) then
									local x, y = -1, -1;
									pcall(function() x, y = entry:GetLocation(); end);
									if x == nil then x = -1 end
									if y == nil then y = -1 end
									table.insert(lines, EVENTS_MARKER.."|WEATHER|"..turn.."|"..i.."|"..typeName.."|"..x..","..y.."|"..msg);
								end
							end
						end);
					end
				end
			end);
		end
	end
	table.insert(lines, EVENTS_MARKER.."|END|"..turn);
	Automation.Log(table.concat(lines, "\n"));
end

-- Game.GetWinningTeam() (confirmed real API) only says which TEAM won, not
-- which victory condition triggered -- there's no single "what won it"
-- accessor. This checks each condition in a fixed priority order using the
-- same per-civ state civ6-mcp's own victory.py query and the base game's
-- Victory Progress screen already rely on (science/diplomatic victory
-- points, culture dominance, religion majority, original-capital
-- ownership). Best-effort: if the winner satisfies more than one
-- condition simultaneously the first match below wins, and ties/edge cases
-- (e.g. a conquest that finishes the same turn a space launch completes)
-- aren't disambiguated further.
local function DetermineVictoryType(winnerID)
	local winner = Players[winnerID];
	if winner == nil then return "UNKNOWN"; end

	-- Domination: every other living major has lost their original capital
	-- to the winner, or isn't alive at all.
	local isDomination = true;
	local sawOtherMajor = false;
	pcall(function()
		for k = 0, 62 do
			if k ~= winnerID and Players[k] and Players[k]:IsMajor() then
				if Players[k]:IsAlive() then
					sawOtherMajor = true;
					local lost = false;
					pcall(function()
						local cap = Players[k]:GetCities():GetCapitalCity();
						if cap == nil then
							lost = true;
						else
							lost = cap:IsOriginalCapital() and cap:GetOwner() == winnerID;
						end
					end);
					if not lost then isDomination = false; end
				end
			end
		end
	end);
	if isDomination and sawOtherMajor then return "DOMINATION"; end

	-- Science: space race points reached (same field DumpDemographics
	-- already tracks as scivp/scineeded).
	local sciDone = false;
	pcall(function()
		local st = winner:GetStats();
		local vp = st:GetScienceVictoryPoints();
		local needed = st:GetScienceVictoryPointsTotalNeeded();
		sciDone = needed > 0 and vp >= needed;
	end);
	if sciDone then return "SCIENCE"; end

	-- Diplomatic: hits the required diplomatic victory point threshold
	-- (20 by default; read from GlobalParameters if the ruleset overrides it).
	local diploDone = false;
	pcall(function()
		local vp = winner:GetStats():GetDiplomaticVictoryPoints();
		local needed = 20;
		pcall(function() needed = GameInfo.GlobalParameters["DIPLOMATIC_VICTORY_POINTS_REQUIRED"].Value; end);
		diploDone = vp >= needed;
	end);
	if diploDone then return "DIPLOMATIC"; end

	-- Culture: dominant (more foreign tourists than their domestic) over
	-- every other living major.
	local cultureDone = true;
	local sawOtherForCulture = false;
	pcall(function()
		local wCul = winner:GetCulture();
		for k = 0, 62 do
			if k ~= winnerID and Players[k] and Players[k]:IsMajor() and Players[k]:IsAlive() then
				sawOtherForCulture = true;
				local dominant = false;
				pcall(function() dominant = wCul:IsDominantOver(k); end);
				if not dominant then cultureDone = false; end
			end
		end
	end);
	if cultureDone and sawOtherForCulture then return "CULTURE"; end

	-- Religious: winner's own founded religion is the majority religion in
	-- every other living major's cities.
	local religiousDone = true;
	local sawOtherForReligion = false;
	pcall(function()
		local relType = winner:GetReligion():GetReligionTypeCreated();
		if relType >= 0 then
			for k = 0, 62 do
				if k ~= winnerID and Players[k] and Players[k]:IsMajor() and Players[k]:IsAlive() then
					sawOtherForReligion = true;
					local ok, majRel = pcall(function() return Players[k]:GetReligion():GetReligionInMajorityOfCities(); end);
					if not (ok and majRel == relType) then religiousDone = false; end
				end
			end
		else
			religiousDone = false;
		end
	end);
	if religiousDone and sawOtherForReligion then return "RELIGIOUS"; end

	-- None of the above matched -- most likely a score victory (turn/time
	-- limit reached with no other condition met).
	return "SCORE";
end

-- Whether the game has ended in a victory at all, and if so, who and how.
-- Logged unconditionally every turn once true (no "logged once" flag) --
-- same reasoning as the map-lookup tables above: a flag in memory has no
-- way to know if on-disk data it's guarding got truncated/rotated away.
local function DumpVictoryStatus()
	local turn = Game.GetCurrentGameTurn();
	local winningTeam = -1;
	pcall(function() winningTeam = Game.GetWinningTeam(); end);
	if winningTeam == nil or winningTeam < 0 then return; end

	local winnerIDs = {};
	for i = 0, 62 do
		if Players[i] and Players[i]:IsAlive() then
			local ok, team = pcall(function() return Players[i]:GetTeam(); end);
			if ok and team == winningTeam then table.insert(winnerIDs, i); end
		end
	end

	local vtype = "UNKNOWN";
	if #winnerIDs > 0 then
		local ok, result = pcall(function() return DetermineVictoryType(winnerIDs[1]); end);
		if ok then vtype = result; end
	end

	Automation.Log(MARKER.."|VICTORYACHIEVED|"..turn.."|"..table.concat(winnerIDs, ",").."|"..vtype);
end

local function OnTurnBegin()
	-- Never let a query failure (e.g. an API shape change in some future
	-- patch) propagate and disrupt actual turn processing.
	pcall(DumpTurnStats);
	pcall(DumpMapLookup);
	pcall(DumpMapTiles);
	pcall(DumpUnitStatus);
	pcall(DumpDemographics);
	pcall(DumpReligion);
	pcall(DumpNotableEvents);
	pcall(DumpVictoryStatus);
end

Events.TurnBegin.Add(OnTurnBegin);
GameEvents.OnCombatOccurred.Add(OnCombatOccurred);
