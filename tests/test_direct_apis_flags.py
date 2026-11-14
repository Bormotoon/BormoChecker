from unittest.mock import MagicMock, patch

from bormochecker.direct_apis import DirectAPIs, PravoGovAPI


def test_pravogov_uses_https():
    api = PravoGovAPI()
    assert api.API_URL.startswith("https://")
    assert "http://" not in api.API_URL


def test_direct_apis_default_flags_only_wiki():
    apis = DirectAPIs()
    with patch.object(apis.pravo, "search_acts", return_value=[]) as mock_pravo, \
         patch.object(apis.fgos, "search_fgos", return_value=[]) as mock_fgos, \
         patch.object(apis.mos_ru, "search_mos_ru", return_value=[]) as mock_mos, \
         patch.object(apis.cyberleninka, "search_articles", return_value=[]) as mock_cyber, \
         patch.object(apis.wiki, "search_and_extract", return_value=[]) as mock_wiki, \
         patch.dict("os.environ", {}, clear=True):

        # Make is_applicable return True for all to test flag gating
        apis.pravo.is_applicable = MagicMock(return_value=True)
        apis.fgos.is_applicable = MagicMock(return_value=True)
        apis.mos_ru.is_applicable = MagicMock(return_value=True)
        apis.cyberleninka.is_applicable = MagicMock(return_value=True)

        apis.fetch_candidates("ФГОС закон указ статья")

        mock_pravo.assert_not_called()
        mock_fgos.assert_not_called()
        mock_mos.assert_not_called()
        mock_cyber.assert_not_called()
        mock_wiki.assert_called_once()


def test_direct_apis_explicitly_enabled():
    apis = DirectAPIs()
    with patch.object(apis.pravo, "search_acts", return_value=[]) as mock_pravo, \
         patch.object(apis.fgos, "search_fgos", return_value=[]) as mock_fgos, \
         patch.object(apis.mos_ru, "search_mos_ru", return_value=[]) as mock_mos, \
         patch.object(apis.cyberleninka, "search_articles", return_value=[]) as mock_cyber, \
         patch.object(apis.wiki, "search_and_extract", return_value=[]) as mock_wiki, \
         patch.dict(
             "os.environ",
             {
                 "FACTCHECK_ENABLE_PRAVOGOV": "1",
                 "FACTCHECK_ENABLE_FGOS": "1",
                 "FACTCHECK_ENABLE_MOSRU": "1",
                 "FACTCHECK_ENABLE_CYBERLENINKA": "1",
                 "FACTCHECK_ENABLE_WIKIPEDIA": "0",
             },
             clear=True,
         ):

        apis.pravo.is_applicable = MagicMock(return_value=True)
        apis.fgos.is_applicable = MagicMock(return_value=True)
        apis.mos_ru.is_applicable = MagicMock(return_value=True)
        apis.cyberleninka.is_applicable = MagicMock(return_value=True)

        apis.fetch_candidates("ФГОС закон указ статья")

        mock_pravo.assert_called_once()
        mock_fgos.assert_called_once()
        mock_mos.assert_called_once()
        mock_cyber.assert_called_once()
        mock_wiki.assert_not_called()
