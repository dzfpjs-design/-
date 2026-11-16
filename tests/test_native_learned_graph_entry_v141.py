"""Actual model/modern reception first, then the missing human registration entry."""
from tests.test_geo_codec import pytestmark
from tests.test_geo_pairs import ready
from tests.test_learned_geo_pairs import rasters
from tests.test_native_learned_geo_journey_v138 import actual_learned_journey


def test_actual_learned_candidates_have_closed_real_registration_options(client, rasters):
    value = actual_learned_journey(client, rasters)
    response = client.get(value['base'] + '/graph-event-native-learned-geo-producers/options')
    assert response.status_code == 200, response.text[:300]
    from tests.test_native_graph_pipeline_v136 import wire
    options = wire(response)
    assert options['schema'] == 'graph-event-native-learned-geo-producer-options-v1'
    assert options['instance_id'] == value['release']['instance_id']
    assert options['can_register'] is True and options['sources'] and options['capabilities']
    assert len(options['capabilities']) == 1
    capability = options['capabilities'][0]
    assert capability['model_trace']['pack_id'] == 'changer-r18-binary-v1'
    assert capability['model_trace']['backend'] == 'cpu'
    assert capability['model_trace']['installation_id'] == value['installation']['id']
    assert capability['probability_threshold'] == .5 and capability['alignment'] == 'same_grid'
