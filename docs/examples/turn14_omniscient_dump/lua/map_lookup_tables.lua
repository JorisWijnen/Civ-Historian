-- Static DB lookup tables: terrain/feature/resource index -> type name. GameCore-safe (execute_read).
-- Output lines: TERRAIN|idx|type , FEATURE|idx|type , RESOURCE|idx|type|class
for row in GameInfo.Terrains() do print("TERRAIN|"..row.Index.."|"..row.TerrainType) end
for row in GameInfo.Features() do print("FEATURE|"..row.Index.."|"..row.FeatureType) end
for row in GameInfo.Resources() do print("RESOURCE|"..row.Index.."|"..row.ResourceType.."|"..(row.ResourceClassType or "")) end
print("---END---")
