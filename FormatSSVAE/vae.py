import os
import pyro
import pyro.distributions as dist
import torch
from FormatSSVAE.const import ALL_LETTERS, EOS_CHAR, MAX_STRING_LEN
from FormatSSVAE.neural_net.mlp import NeuralNet
from FormatSSVAE.neural_net.rnn import Encoder, Decoder
from FormatSSVAE.util.convert import chars_to_tensor, strings_to_tensor

class FormatVAE():

    def __init__(self, encoder_hidden_size: int = 128, decoder_hidden_size: int = 64, mlp_hidden_size: int = 16):
        self.encoder_lstm = Encoder(input_size=len(ALL_LETTERS), hidden_size=encoder_hidden_size)
        self.decoder_lstm = Decoder(input_size=len(ALL_LETTERS), hidden_size=decoder_hidden_size, output_size=len(ALL_LETTERS))

        # Latent dimension is <LSTM decoder hidden size x LSTM decoder num layers x 2>
        self.latent_dim = decoder_hidden_size * 1 * 2
        self.decoder_hidden_size = decoder_hidden_size

        # MLP's input size is <LSTM encoder hidden size x LSTM encoder num layers x 2>
        mlp_input_size = encoder_hidden_size * 1 * 2
        self.loc_mlp = NeuralNet(input_size=mlp_input_size, hidden_size=mlp_hidden_size, output_size=self.latent_dim)
        self.scale_mlp = NeuralNet(input_size=mlp_input_size, hidden_size=mlp_hidden_size, output_size=self.latent_dim)
    

    def model(self, x):
        """
        1. Sample z from independent multivariate normal N(0,1)
        2. Pass z as the hidden state to the LSTM decoder
        3. Observe each one hot vector generated by the LSTM decoder
        """
        pyro.module("decoder_lstm", self.decoder_lstm)

        if x is not None: batch_size, x_tensor = self._preprocess_input(x)
        else: batch_size, x_tensor = 1, [None] * MAX_STRING_LEN
        outputs = [""] * batch_size
        
        with pyro.plate("data"):
            loc, scale = torch.zeros(batch_size, self.latent_dim), torch.ones(batch_size, self.latent_dim)
            z = pyro.sample("z", dist.Normal(loc, scale).to_event(1))

            # z: <batch size, latent dim> => 2 x <LSTM decoder num layers x batch size x LSTM decoder hidden size>
            decoder_hidden_state = (z[:,:self.decoder_hidden_size].unsqueeze(0), z[:,self.decoder_hidden_size:].unsqueeze(0))
            decoder_input = chars_to_tensor(chars=[EOS_CHAR]*batch_size, letter_set=ALL_LETTERS)

            # Step decoder MAX_STRING_LEN times using EOS as input and observe every output
            for i in range(MAX_STRING_LEN):
                categorical_probs, decoder_hidden_state = self.decoder_lstm.forward(decoder_input, decoder_hidden_state)
                decoder_input = pyro.sample(f"x_{i}", dist.OneHotCategorical(probs=categorical_probs.squeeze(0)), obs=x_tensor[i]).unsqueeze(0)
                for j in range(len(outputs)): 
                    outputs[j] += ALL_LETTERS[decoder_input.squeeze(0)[j].nonzero()]
            
            # Replace every character after the first occurrence EOS in all names
            outputs = list(map(lambda string: string[:string.find(EOS_CHAR)] if string.find(EOS_CHAR) != -1 else string, outputs))
            return outputs
            
            
    def guide(self, x):
        """
        1. Pass x as the input data to the LSTM encoder
        2. Pass the LSTM encoder's last hidden state into the MLP to obtain f(x), g(x)
        3. Sample z from multivariate normal N(f(x),g(x))
        """
        pyro.module("encoder_lstm", self.encoder_lstm)
        pyro.module("loc_mlp", self.loc_mlp)
        pyro.module("scale_mlp", self.scale_mlp)

        batch_size, x_tensor = self._preprocess_input(x)
        with pyro.plate("data"):
            encoder_hidden_state = self.encoder_lstm.init_hidden(batch_size=batch_size)
            for i in range(MAX_STRING_LEN):
                _, encoder_hidden_state = self.encoder_lstm.forward(x_tensor[i].unsqueeze(0), encoder_hidden_state)

            encoder_hidden = encoder_hidden_state[0].view(batch_size, -1)
            encoder_cell = encoder_hidden_state[1].view(batch_size, -1)
            flattened_lstm_output = torch.cat((encoder_hidden, encoder_cell), dim=1)
            loc = self.loc_mlp.forward(flattened_lstm_output)
            scale = self.loc_mlp.forward(flattened_lstm_output)

            z = pyro.sample("z", dist.Normal(loc, scale).to_event(1))
    

    def load_checkpoint(self, folder="nn_model", filename="checkpoint.pth.tar"):
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath): 
            raise Exception(f"No model in path {folder}")
        save_content = torch.load(filepath)
        self.encoder_lstm.load_state_dict(save_content['encoder_lstm'])
        self.decoder_lstm.load_state_dict(save_content['decoder_lstm'])
        self.loc_mlp.load_state_dict(save_content['loc_mlp'])
        self.scale_mlp.load_state_dict(save_content['scale_mlp'])


    def save_checkpoint(self, folder="nn_model", filename="checkpoint.pth.tar"):
        filepath = os.path.join(folder, filename)
        if not os.path.exists(folder):
            os.mkdir(folder)
        save_content = {
            'encoder_lstm' : self.encoder_lstm.state_dict(),
            'decoder_lstm' : self.decoder_lstm.state_dict(),
            'loc_mlp' : self.loc_mlp.state_dict(),
            'scale_mlp': self.scale_mlp.state_dict()
        }
        torch.save(save_content, filepath)


    def _preprocess_input(self, x):
        """
        Returns: batch_size and tensorized input
        """
        batch_size = len(x)
        x_tensor = strings_to_tensor(strings=list(map(lambda s: s + EOS_CHAR, x)), letter_set=ALL_LETTERS, tensor_len=MAX_STRING_LEN)
        return batch_size, x_tensor
