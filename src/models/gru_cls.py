__author__ = 'Richard Diehl Martinez'

'''
A recurrent model that reads in attention distribution one attention-distribution
at a time. This is the standard model we use for the majority of our experiments.
'''

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

class AttentionLayer(nn.Module):
    def __init__(self, units, input_dim, hidden_dim, directions):
        super().__init__()
        
        # attentional layers
        self.W1 = nn.Linear(directions * hidden_dim, units)
        self.W2 = nn.Linear(input_dim, units)
        self.V = nn.Linear(units, 1)
        
    def forward(self, enc_output, dec_hidden, attention_mask=None):
        # score: [B, N, 1]
        scores = self.V(torch.tanh(self.W1(dec_hidden.unsqueeze(1)) + self.W2(enc_output)))

        if attention_mask is not None:
            if len(attention_mask.shape) == 2:
                attention_mask = attention_mask.unsqueeze(dim=2)
            scores += attention_mask * -10000.0

        # attn_weights: [B, N, 1]
        attn_weights = torch.softmax(scores, dim=1)

        return attn_weights

class GRUClassifier(nn.Module):
    def __init__(self, final_task_params, attention_params=None, output_dim=1, bidirectional=True):
        '''
        A basic GRU model that takes in as input attention distributions and
        outputs a prediction for the type of bias. We always assume that the
        input contains some form of bias (either epistemological:0  or framing:1).
        As a result, we frame the classification as a binary classification task.

        Args:
            * params (dictionary of params): These are the params for the classification
                 task passed in from a params object. See src/params for a definition
                 of this class.
            * output_dim (int): Set to 1 since we are doing binary classification.
            * bidirectional (bool): Whether the GRU should be bidirecional.
        '''
        super().__init__()

        self.attentional = final_task_params.get("attention", True)
        
        if self.attentional:
            assert(bool(attention_params)), "\'attention_params\' cannot be None if \'attentional\' is True."

        # NOTE: we need the input dim to be equal to the max seq len
        input_dim = final_task_params['input_dim']
        if attention_params:
            if "n_components" in attention_params:
                input_dim = attention_params["n_components"]
            elif attention_params['reducer'] == 'concat':
                input_dim = n_components * len(attention_params['layers'])
        
        self.input_dim = input_dim
        self.hidden_dim = final_task_params['hidden_dim']
        self.output_dim = output_dim

        self.n_layers = final_task_params['n_layers']
        self.dropout = final_task_params['dropout']
        self.gru = nn.GRU(self.input_dim, self.hidden_dim,
                          self.n_layers,
                          batch_first=True,
                          dropout=self.dropout,
                          bidirectional=True)

        self.n_directions = 2 if bidirectional else 1

        # attentional layers
        context_dim = 0
        if self.attentional:
            self.attention_layer = AttentionLayer(
                final_task_params.get("attention_units", 512),
                self.input_dim,
                self.hidden_dim,
                self.n_directions
            )
            context_dim = self.input_dim

        self.fc = nn.Linear(context_dim + self.n_directions * self.hidden_dim, self.output_dim)
        self.relu = nn.ReLU()

    def forward(self, x, lengths=None, attention_mask=None, **kwargs):

        # packed_enc_output: [B, ?, E]
        if lengths is not None:
            packed_enc_output = pack_padded_sequence(x, lengths, batch_first=True, enforce_sorted=False)
        else:
            packed_enc_output = x

        # dec_hidden: [B, 2 * D]
        dec_output, dec_hidden = self.gru(packed_enc_output)
        dec_hidden = torch.cat([dec_hidden[-2], dec_hidden[-1]], dim=-1)

        # padded_enc_output: [B, N, E]
        if lengths is not None:
            padded_enc_output, _ = pad_packed_sequence(
                packed_enc_output, 
                batch_first=True, 
                padding_value=0.,
                total_length=x.shape[1]
            )
        else:
            padded_enc_output = x

        if self.attentional:
            
            if attention_mask is not None:
                attention_mask = attention_mask.to(dtype=torch.float)
                
            attn_weights = self.attention_layer(padded_enc_output, dec_hidden, attention_mask)
            context = torch.sum(attn_weights * padded_enc_output, dim=1)
            output = self.fc(torch.cat([context, dec_hidden], dim=-1))
        else:
            output = self.fc(dec_hidden)

        return output
