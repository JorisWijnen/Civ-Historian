-- Full map tile dump, unconditional (no PlayersVisibility/IsRevealed check at all).
-- One print() per map row to keep message count low (74x46 map -> 46 messages, not 3404).
-- GameCore-safe (execute_read). Decode terrain/feature/resource indices via map_lookup_tables.lua output.
-- Output: ROW|y|terrainIdx,ownerIdx,hills,mtn,water,river,coastal,featureIdx,resourceIdx,continentIdx;...(one group per x)
local w, h = Map.GetGridSize()
for y = 0, h - 1 do
  local parts = {}
  for x = 0, w - 1 do
    local plot = Map.GetPlot(x, y)
    if plot then
      local terrainIdx = plot:GetTerrainType()
      local ownerIdx = plot:GetOwner()
      local hills = plot:IsHills() and 1 or 0
      local mtn = plot:IsMountain() and 1 or 0
      local water = plot:IsWater() and 1 or 0
      local river = plot:IsRiver() and 1 or 0
      local coastal = plot:IsCoastalLand() and 1 or 0
      local featureIdx = plot:GetFeatureType()
      local resourceIdx = plot:GetResourceType()
      local continent = plot:GetContinentType()
      table.insert(parts, terrainIdx..","..ownerIdx..","..hills..","..mtn..","..water..","..river..","..coastal..","..featureIdx..","..resourceIdx..","..continent)
    end
  end
  print("ROW|"..y.."|"..table.concat(parts, ";"))
end
print("---END---")
