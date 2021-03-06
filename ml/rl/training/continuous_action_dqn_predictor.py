#!/usr/bin/env python3

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np

from caffe2.python.predictor.predictor_exporter import PredictorExportMeta
from caffe2.python import model_helper
from caffe2.python import workspace

from ml.rl.training.rl_predictor import RLPredictor
from ml.rl.preprocessing.preprocessor_net import PreprocessorNet, MISSING_VALUE

import logging
logger = logging.getLogger(__name__)


class ContinuousActionDQNPredictor(RLPredictor):
    def predict(self, states, actions):
        """ Returns values for each state
        :param states states as feature -> value dict
        """
        previous_workspace = workspace.CurrentWorkspace()
        workspace.SwitchWorkspace(self._workspace_id)
        for name, value in states.items():
            workspace.FeedBlob(name, np.atleast_1d(value).astype(np.float32))
        for name, value in actions.items():
            workspace.FeedBlob(name, np.atleast_1d(value).astype(np.float32))
        workspace.RunNetOnce(self._net)
        result = {'Q': workspace.FetchBlob(self._output_blobs[0])}
        workspace.SwitchWorkspace(previous_workspace)
        return result

    def get_predictor_export_meta(self):
        return PredictorExportMeta(
            self._net, self._parameters + self._input_blobs, [],
            self._output_blobs
        )

    @classmethod
    def from_trainers(
        cls, trainer, state_features, action_features,
        state_normalization_parameters, action_normalization_parameters
    ):
        """ Creates DiscreteActionPredictor from a list of action trainers

        :param trainer DiscreteActionTrainer
        :param state_features list of state feature names
        :param action_features list of action feature names
        """
        # ensure state and action IDs have no intersection
        assert (len(set(state_features) & set(action_features)) == 0)
        normalization_parameters = dict(
            list(state_normalization_parameters.items()) +
            list(action_normalization_parameters.items())
        )
        input_blobs = state_features + action_features

        model = model_helper.ModelHelper(name="predictor")
        net = model.net
        normalizer = PreprocessorNet(net)
        parameters = normalizer.parameters[:]
        normalized_input_blobs = []
        zero = "ZERO_from_trainers"
        workspace.FeedBlob(zero, np.array(0))
        parameters.append(zero)
        for input_blob in input_blobs:
            workspace.FeedBlob(
                input_blob, MISSING_VALUE * np.ones(1, dtype=np.float32)
            )
            reshaped_input_blob = input_blob + "_reshaped"
            net.Reshape(
                [input_blob],
                [reshaped_input_blob, input_blob + "_original_shape"],
                shape=[-1, 1]
            )
            normalized_input_blob, blob_parameters = normalizer.preprocess_blob(
                reshaped_input_blob, normalization_parameters[input_blob]
            )
            parameters.extend(blob_parameters)
            normalized_input_blobs.append(normalized_input_blob)

        input_blob = "PredictorInput"
        output_dim = "PredictorOutputDim"
        for i, inp in enumerate(normalized_input_blobs):
            logger.info("input# {}: {}".format(i, inp))
        net.Concat(normalized_input_blobs, [input_blob, output_dim], axis=1)

        q_values = "Q"
        workspace.FeedBlob(q_values, np.zeros(1, dtype=np.float32))
        parameters.extend(trainer.build_predictor(model, input_blob, q_values))

        output_blobs = [q_values]

        workspace.RunNetOnce(model.param_init_net)
        workspace.CreateNet(net)
        predictor = cls(
            net, input_blobs, output_blobs, parameters,
            workspace.CurrentWorkspace()
        )
        return predictor
