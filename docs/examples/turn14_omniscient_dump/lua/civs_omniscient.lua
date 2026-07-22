-- Full per-major-civ stats, unconditional (no HasMet gating). Run in InGame context.
-- Output lines: CIV|playerID|civName|leaderName|score=N|cities=N|pop=N|mil=N|techs=N|civics=N|gold=N
for i = 0, 62 do
  if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local name = Locale.Lookup(cfg:GetCivilizationShortDescription())
    local leader = Locale.Lookup(cfg:GetLeaderName())
    local p = Players[i]
    local score = p:GetScore()
    local nCities, totalPop = 0, 0
    for _, c in p:GetCities():Members() do nCities = nCities + 1; totalPop = totalPop + c:GetPopulation() end
    local st = p:GetStats()
    local mil = st:GetMilitaryStrength()
    local techs = st:GetNumTechsResearched()
    local civics = st:GetNumCivicsCompleted()
    local gold = p:GetTreasury():GetGoldBalance()
    print("CIV|"..i.."|"..name.."|"..leader.."|score="..score.."|cities="..nCities..
      "|pop="..totalPop.."|mil="..mil.."|techs="..techs.."|civics="..civics.."|gold="..gold)
  end
end
print("---END---")
