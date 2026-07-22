-- All alive players (majors, city-states/minors, barbarians), unconditional. GameCore-safe (execute_read).
-- Output lines: PLAYER|playerID|MAJOR|MINOR|BARB|name
for i = 0, 63 do
  local p = Players[i]
  if p ~= nil then
    local alive = p:IsAlive()
    if alive then
      local cfg = PlayerConfigurations[i]
      local name = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?"
      local kind = "MINOR"
      if p:IsMajor() then kind = "MAJOR" elseif p:IsBarbarian() then kind = "BARB" end
      print("PLAYER|"..i.."|"..kind.."|"..name)
    end
  end
end
print("---END---")
